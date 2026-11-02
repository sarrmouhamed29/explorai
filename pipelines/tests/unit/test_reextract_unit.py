"""``reextract`` and ``process-pending`` without a database: fakes record the reads."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from explorai_db.blobs import BlobStore
from explorai_pipelines.config import PipelineSettings
from explorai_pipelines.extract.models import ExtractedDocument, ExtractedSection
from explorai_pipelines.flows import process_pending, runtime
from explorai_pipelines.ingest import Extractor, reextract
from explorai_pipelines.repository import ArtifactRef, Repository, SavedVersion

PDF = b"%PDF-1.7 fake"
TEXT = "Dense retrieval and sparse retrieval complement each other in practice."


def ref(key: str) -> ArtifactRef:
    return ArtifactRef(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        external_id=key,
        url=f"upload:{key}",
        owner_id=None,
        storage_key=key,
    )


class FakeRepo:
    """Two PDF artifacts: ``versioned`` has a pdf version, ``fresh`` has none.

    Like the real query, ``artifacts_missing_version`` for the html extractor
    returns both: no PDF ever gets an html version.
    """

    def __init__(self) -> None:
        self.versioned = ref("versioned")
        self.fresh = ref("fresh")
        self.saved: list[uuid.UUID] = []
        self.lock_keys: list[int] = []

    @contextmanager
    def advisory_lock(self, key: int) -> Iterator[bool]:
        self.lock_keys.append(key)
        yield True

    def artifacts_missing_version(self, extractor_version: str) -> list[ArtifactRef]:
        if extractor_version == "pdf-test":
            return [self.fresh]
        return [self.versioned, self.fresh]

    def artifacts_without_any_version(self) -> list[ArtifactRef]:
        return [self.fresh]

    def load_signatures(self, owner_id: uuid.UUID | None) -> list[tuple[str, bytes]]:
        return []

    def find_exact_duplicate(self, *args: Any) -> uuid.UUID | None:
        return None

    def document_version_ids(self, *args: Any) -> set[str]:
        return set()

    def save_version(self, **kwargs: Any) -> SavedVersion:
        self.saved.append(kwargs["artifact_id"])
        return SavedVersion(version_id=uuid.uuid4(), document_created=False)


class SpyStore:
    def __init__(self) -> None:
        self.reads: list[str] = []

    def get(self, key: str) -> bytes:
        self.reads.append(key)
        return PDF


def extract(data: bytes, url: str | None, timeout: float) -> ExtractedDocument:
    return ExtractedDocument(title=None, sections=[ExtractedSection(None, TEXT)])


EXTRACTORS = {
    "pdf": Extractor(version="pdf-test", kind="pdf", run=extract),
    "html": Extractor(version="html-test", kind="html", run=extract),
}


@pytest.fixture
def settings(tmp_path: Path) -> PipelineSettings:
    return PipelineSettings(
        database_url="postgresql://u:p@127.0.0.1:5432/db",  # type: ignore[arg-type]
        blob_dir=tmp_path,
        contact_email="me@example.org",
        extract_timeout_seconds=60,
    )


def run(
    repo: FakeRepo, store: SpyStore, settings: PipelineSettings, **kwargs: Any
) -> dict[str, int]:
    return reextract(
        repo=cast(Repository, repo),
        store=cast(BlobStore, store),
        settings=settings,
        extractors=EXTRACTORS,
        **kwargs,
    )


def test_unversioned_pass_never_reads_an_artifact_that_has_a_version(
    settings: PipelineSettings,
) -> None:
    repo, store = FakeRepo(), SpyStore()

    counters = run(repo, store, settings, only_unversioned=True)

    assert store.reads == ["fresh"]
    assert repo.saved == [repo.fresh.id]
    assert counters["new_version"] == 1


def test_unversioned_pass_keeps_the_catch_up_lock(settings: PipelineSettings) -> None:
    from explorai_pipelines.ingest import CATCH_UP_LOCK

    repo = FakeRepo()

    run(repo, SpyStore(), settings, only_unversioned=True)

    assert repo.lock_keys == [CATCH_UP_LOCK]


def test_full_pass_still_looks_at_every_artifact_missing_a_current_version(
    settings: PipelineSettings,
) -> None:
    # The nightly catch-up and the manual command handle extractor upgrades.
    repo, store = FakeRepo(), SpyStore()

    run(repo, store, settings)

    assert sorted(store.reads) == ["fresh", "fresh", "versioned"]
    assert repo.saved == [repo.fresh.id]


def test_process_pending_asks_for_unversioned_artifacts_only(
    monkeypatch: pytest.MonkeyPatch, settings: PipelineSettings
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_reextract(**kwargs: Any) -> dict[str, int]:
        calls.append(kwargs)
        return {}

    built = runtime.Runtime(
        settings=settings,
        repo=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        arxiv=None,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(runtime, "build_runtime", lambda: built)
    monkeypatch.setattr(process_pending, "reextract", fake_reextract)
    monkeypatch.setattr(process_pending, "collect_blobs", lambda repo, store: 0)

    counters = process_pending.process_pending_flow.fn()

    assert [call["only_unversioned"] for call in calls] == [True]
    assert counters == {"blobs_deleted": 0}
