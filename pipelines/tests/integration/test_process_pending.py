import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from prefect.testing.utilities import prefect_test_harness
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from explorai_db.blobs import LocalBlobStore
from explorai_db.models import Document, DocumentVersion, RawArtifact, Source, User
from explorai_pipelines.config import PipelineSettings
from explorai_pipelines.flows import process_pending, runtime
from explorai_pipelines.ingest import CATCH_UP_LOCK, collect_blobs, reextract
from explorai_pipelines.repository import Repository
from explorai_pipelines.testing import make_pdf, one_column_page

pytestmark = pytest.mark.integration

LOREM = "Dense retrieval and sparse retrieval complement each other in practice. "


@pytest.fixture(scope="module", autouse=True)
def prefect_harness() -> Iterator[None]:
    with prefect_test_harness():
        yield


def age(store_root: Path, key: str, seconds: float) -> None:
    moment = time.time() - seconds
    os.utime(store_root / key[:2] / key, (moment, moment))


def add_upload(
    factory: sessionmaker[Session], store: LocalBlobStore, title: str = "My paper"
) -> uuid.UUID:
    """Insert what the API leaves behind: artifact and titled document, no version."""
    data = make_pdf([one_column_page([f"{title}. {LOREM * 2}"])])
    with factory.begin() as session:
        user = User(email=f"{uuid.uuid4().hex}@example.org", password_hash="x")
        session.add(user)
        session.flush()
        source_id = session.scalars(
            select(Source.id).where(Source.kind == "upload")
        ).one()
        document = Document(
            source_id=source_id, external_id="up-1", title=title, owner_id=user.id
        )
        session.add(document)
        session.add(
            RawArtifact(
                source_id=source_id,
                external_id="up-1",
                url="upload://up-1.pdf",
                sha256="0" * 64,
                storage_key=store.put(data),
                owner_id=user.id,
            )
        )
        session.flush()
        return document.id


def test_uploaded_artifact_is_processed_into_the_existing_document(
    monkeypatch: pytest.MonkeyPatch,
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    session_factory: sessionmaker[Session],
) -> None:
    document_id = add_upload(session_factory, store)
    built = runtime.Runtime(
        settings=settings,
        repo=repo,
        store=store,
        client=None,  # type: ignore[arg-type]
        arxiv=None,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(runtime, "build_runtime", lambda: built)

    counters = process_pending.process_pending_flow()

    assert counters["ingested"] + counters["new_version"] == 1
    assert counters["blobs_deleted"] == 0
    with session_factory() as session:
        versions = session.scalars(select(DocumentVersion)).all()
        assert [v.document_id for v in versions] == [document_id]
        assert session.get(Document, document_id).title == "My paper"  # type: ignore[union-attr]


def test_second_runner_is_skipped_while_the_lock_is_held(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    session_factory: sessionmaker[Session],
) -> None:
    add_upload(session_factory, store)

    with repo.advisory_lock(CATCH_UP_LOCK) as holder:
        assert holder is True
        counters = reextract(repo=repo, store=store, settings=settings)
        assert counters["skipped"] == 1
        assert sum(v for k, v in counters.items() if k != "skipped") == 0
        with session_factory() as session:
            assert session.scalars(select(DocumentVersion)).all() == []

    # Released on exit: the next runner works.
    counters = reextract(repo=repo, store=store, settings=settings)
    assert counters["skipped"] == 0
    assert counters["new_version"] + counters["ingested"] == 1


def test_lock_is_released_when_the_work_raises(repo: Repository) -> None:
    with pytest.raises(RuntimeError), repo.advisory_lock(CATCH_UP_LOCK):
        raise RuntimeError("boom")
    with repo.advisory_lock(CATCH_UP_LOCK) as acquired:
        assert acquired is True


def test_unreferenced_old_blob_is_collected(
    repo: Repository, store: LocalBlobStore, tmp_path: Path
) -> None:
    key = store.put(b"orphan")
    age(tmp_path / "blobs", key, 7200)

    assert collect_blobs(repo, store) == 1
    with pytest.raises(FileNotFoundError):
        store.get(key)


def test_recent_or_referenced_blob_is_kept(
    repo: Repository,
    store: LocalBlobStore,
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    recent = store.put(b"recent orphan")
    add_upload(session_factory, store)
    with session_factory() as session:
        referenced_key = session.scalars(select(RawArtifact.storage_key)).one()
    age(tmp_path / "blobs", referenced_key, 7200)

    assert collect_blobs(repo, store) == 0
    assert store.get(recent) == b"recent orphan"
    assert store.get(referenced_key)
    assert repo.referenced_storage_keys() == {referenced_key}


def test_blob_refreshed_after_listing_survives_collection(
    repo: Repository, store: LocalBlobStore, tmp_path: Path
) -> None:
    key = store.put(b"orphan")
    age(tmp_path / "blobs", key, 7200)

    class ReuploadAfterListing:
        def __getattr__(self, name: str) -> object:
            return getattr(store, name)

        def keys_older_than(self, seconds: float) -> list[str]:
            keys = store.keys_older_than(seconds)
            store.put(b"orphan")  # identical upload, rows not committed yet
            return keys

    assert collect_blobs(repo, ReuploadAfterListing()) == 0  # type: ignore[arg-type]
    assert store.get(key) == b"orphan"


def test_process_pending_does_not_read_artifacts_that_have_a_version(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    session_factory: sessionmaker[Session],
) -> None:
    # Processed before: it has a pdf version, and like every PDF no html version.
    add_upload(session_factory, store, title="Old paper")
    first = reextract(repo=repo, store=store, settings=settings, only_unversioned=True)
    assert first["ingested"] + first["new_version"] == 1
    with session_factory() as session:
        old_key = session.scalars(select(RawArtifact.storage_key)).one()
    add_upload(session_factory, store, title="New paper")
    reads: list[str] = []

    class SpyStore:
        def __getattr__(self, name: str) -> object:
            return getattr(store, name)

        def get(self, key: str) -> bytes:
            reads.append(key)
            return store.get(key)

    counters = reextract(
        repo=repo,
        store=SpyStore(),  # type: ignore[arg-type]
        settings=settings,
        only_unversioned=True,
    )

    assert counters["ingested"] + counters["new_version"] == 1
    assert old_key not in reads
    assert len(reads) == 1
    assert [ref.storage_key for ref in repo.artifacts_without_any_version()] == []
