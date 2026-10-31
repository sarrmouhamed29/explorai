import functools
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from explorai_db.models import (
    Document,
    DocumentVersion,
    IngestionRun,
    RawArtifact,
    Section,
)
from explorai_pipelines.config import PipelineSettings
from explorai_pipelines.extract.models import ExtractedDocument, ExtractedSection
from explorai_pipelines.extract.pdf import PDF_EXTRACTOR_VERSION, extract_pdf
from explorai_pipelines.ingest import (
    DEFAULT_EXTRACTORS,
    Extractor,
    Outcome,
    ingest_item,
    reextract,
    run_batch,
)
from explorai_pipelines.netsafe import FetchResult
from explorai_pipelines.repository import Repository
from explorai_pipelines.sources.base import SourceItem
from explorai_pipelines.storage import LocalBlobStore
from explorai_pipelines.testing import image_only_pdf, make_pdf

pytestmark = pytest.mark.integration

LOREM = (
    "Retrieval augmented generation grounds answers in documents. "
    "Chunking, embedding and reranking all influence the final quality. "
)
TABLES = (RawArtifact, Document, DocumentVersion, Section)

# In-process extractors keep these tests fast; one test uses the real isolated one.
IN_PROCESS = {
    "pdf": Extractor(
        version=PDF_EXTRACTOR_VERSION,
        kind="pdf",
        run=lambda data, url, timeout: extract_pdf(data),
    ),
    "html": DEFAULT_EXTRACTORS["html"],
}


@functools.cache
def pdf_bytes(text: str) -> bytes:
    # Cached: PyMuPDF embeds a random id, so rebuilding would change the bytes.
    return make_pdf([[(60, 80.0, 535, 780.0, text)]])


def item(external_id: str) -> SourceItem:
    return SourceItem(
        external_id=external_id, url=f"https://arxiv.org/pdf/{external_id}"
    )


def fetched(content: bytes) -> FetchResult:
    return FetchResult(content=content, content_type="application/pdf", final_url="u")


def fetch_of(content: bytes) -> Callable[[], FetchResult]:
    return lambda: fetched(content)


def count(factory: sessionmaker[Session], model: type[Any]) -> int:
    with factory() as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def snapshot(factory: sessionmaker[Session]) -> dict[str, set[Any]]:
    with factory() as session:
        return {
            model.__tablename__: set(session.scalars(select(model.id)))
            for model in TABLES
        }


def ingest(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    source_id: Any,
    work_item: SourceItem,
    content: bytes,
    **kwargs: Any,
) -> Any:
    return ingest_item(
        work_item,
        fetched(content),
        repo=repo,
        store=store,
        source_id=source_id,
        settings=settings,
        extractors=kwargs.pop("extractors", IN_PROCESS),
        **kwargs,
    )


@pytest.fixture
def source_id(repo: Repository) -> Any:
    return repo.get_or_create_source("arxiv", "test", {})


def test_ingest_creates_document_version_and_sections(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    source_id: Any,
    session_factory: sessionmaker[Session],
) -> None:
    result = ingest(
        repo, store, settings, source_id, item("2401.00001"), pdf_bytes(LOREM)
    )

    assert result.outcome is Outcome.INGESTED
    with session_factory() as session:
        version = session.scalars(select(DocumentVersion)).one()
        assert version.status == "ok"
        assert version.extractor_version == PDF_EXTRACTOR_VERSION
        assert version.text_hash and version.minhash_signature
        assert session.scalars(select(Section)).all()
        artifact = session.scalars(select(RawArtifact)).one()
        assert store.get(artifact.storage_key) == pdf_bytes(LOREM)
        assert artifact.owner_id is None


def test_replaying_same_batch_changes_nothing(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    source_id: Any,
    session_factory: sessionmaker[Session],
) -> None:
    work = [
        (item(f"2401.0000{i}"), fetch_of(pdf_bytes(f"Paper {i}. {LOREM * 3}")))
        for i in range(3)
    ]

    def batch() -> Any:
        return run_batch(
            work,
            repo=repo,
            store=store,
            source_id=source_id,
            settings=settings,
            extractors=IN_PROCESS,
        )

    first = batch()
    before = snapshot(session_factory)
    second = batch()

    assert first.counters["ingested"] == 3
    assert second.counters["unchanged"] == 3
    assert snapshot(session_factory) == before


def test_changed_content_creates_new_version_of_same_document(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    source_id: Any,
    session_factory: sessionmaker[Session],
) -> None:
    ingest(repo, store, settings, source_id, item("2401.00002"), pdf_bytes(LOREM))
    result = ingest(
        repo,
        store,
        settings,
        source_id,
        item("2401.00002"),
        pdf_bytes(LOREM + "A revised sentence was added in version two."),
    )

    assert result.outcome is Outcome.NEW_VERSION
    assert count(session_factory, Document) == 1
    assert count(session_factory, RawArtifact) == 2
    assert count(session_factory, DocumentVersion) == 2


def test_reextract_with_new_extractor_version_needs_no_network(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    source_id: Any,
    session_factory: sessionmaker[Session],
) -> None:
    ingest(repo, store, settings, source_id, item("2401.00003"), pdf_bytes(LOREM))
    newer = {
        "pdf": Extractor(
            version="pdf-2",
            kind="pdf",
            run=lambda data, url, timeout: extract_pdf(data),
        ),
        "html": DEFAULT_EXTRACTORS["html"],
    }

    counters = reextract(repo=repo, store=store, extractors=newer, settings=settings)

    assert counters["new_version"] == 1
    with session_factory() as session:
        versions = session.scalars(select(DocumentVersion.extractor_version)).all()
    assert sorted(versions) == ["pdf-1", "pdf-2"]
    assert count(session_factory, RawArtifact) == 1
    again = reextract(repo=repo, store=store, extractors=newer, settings=settings)
    assert again["new_version"] == 0


def test_scanned_pdf_marked_unextractable_and_batch_continues(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    source_id: Any,
    session_factory: sessionmaker[Session],
) -> None:
    work: list[tuple[SourceItem, Callable[[], FetchResult]]] = [
        (item("2401.00010"), lambda: fetched(pdf_bytes(LOREM))),
        (item("2401.00011"), lambda: fetched(image_only_pdf())),
        (item("2401.00012"), lambda: fetched(pdf_bytes("Another paper. " + LOREM * 2))),
    ]

    report = run_batch(
        work,
        repo=repo,
        store=store,
        source_id=source_id,
        settings=settings,
        extractors=IN_PROCESS,
    )

    assert report.counters["ingested"] == 2
    assert report.counters["unextractable"] == 1
    assert report.status == "completed"
    with session_factory() as session:
        statuses = sorted(session.scalars(select(DocumentVersion.status)))
        assert statuses == ["ok", "ok", "unextractable"]
        run = session.scalars(select(IngestionRun)).one()
        assert run.finished_at is not None


def test_failed_fetch_is_recorded_and_batch_continues(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    source_id: Any,
) -> None:
    def boom() -> FetchResult:
        raise OSError("connection reset")

    work: list[tuple[SourceItem, Callable[[], FetchResult]]] = [
        (item("2401.00020"), boom),
        (item("2401.00021"), lambda: fetched(pdf_bytes(LOREM))),
    ]

    report = run_batch(
        work,
        repo=repo,
        store=store,
        source_id=source_id,
        settings=settings,
        extractors=IN_PROCESS,
    )

    assert report.counters["failed"] == 1
    assert report.counters["ingested"] == 1
    assert report.status == "completed_with_errors"
    assert "2401.00020" in report.errors[0]


def test_failure_in_one_transaction_leaves_no_partial_rows(
    repo: Repository,
    source_id: Any,
    session_factory: sessionmaker[Session],
) -> None:
    artifact_id = repo.store_artifact(
        source_id=source_id,
        external_id="2401.00030",
        url="u",
        sha256="c" * 64,
        storage_key="c" * 64,
        owner_id=None,
    )
    # PostgreSQL text columns reject NUL: the insert fails after the document and
    # the version were already flushed in the same transaction.
    poisoned = [ExtractedSection(heading=None, text="a" + chr(0) + "b")]

    with pytest.raises(DBAPIError):
        repo.save_version(
            source_id=source_id,
            external_id="2401.00030",
            owner_id=None,
            title="t",
            artifact_id=artifact_id,
            extractor_version="pdf-1",
            status="ok",
            text_hash="d" * 64,
            signature=b"x",
            duplicate_of=None,
            sections=poisoned,
        )

    assert count(session_factory, Document) == 0
    assert count(session_factory, DocumentVersion) == 0
    assert count(session_factory, Section) == 0


def test_unexpected_extractor_error_is_failed_then_retried_next_run(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    source_id: Any,
    session_factory: sessionmaker[Session],
) -> None:
    def crash(data: bytes, url: str | None, timeout: float) -> ExtractedDocument:
        raise RuntimeError("parser crashed")

    broken = {
        "pdf": Extractor(version=PDF_EXTRACTOR_VERSION, kind="pdf", run=crash),
        "html": DEFAULT_EXTRACTORS["html"],
    }

    result = ingest(
        repo,
        store,
        settings,
        source_id,
        item("2401.00031"),
        pdf_bytes(LOREM),
        extractors=broken,
    )

    assert result.outcome is Outcome.FAILED
    assert result.detail and "parser crashed" in result.detail
    assert count(session_factory, DocumentVersion) == 0
    assert count(session_factory, RawArtifact) == 1  # the raw bytes are kept

    retry = ingest(
        repo, store, settings, source_id, item("2401.00031"), pdf_bytes(LOREM)
    )
    assert retry.outcome is Outcome.INGESTED
    assert count(session_factory, RawArtifact) == 1
    assert count(session_factory, DocumentVersion) == 1


def test_control_characters_in_extracted_text_and_headings_are_stripped(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    source_id: Any,
    session_factory: sessionmaker[Session],
) -> None:
    dirty = Extractor(
        version=PDF_EXTRACTOR_VERSION,
        kind="pdf",
        run=lambda data, url, timeout: ExtractedDocument(
            title="t",
            sections=[
                ExtractedSection(
                    heading="Head" + chr(0) + "ing",
                    text="body" + chr(0) + " text that is long enough to keep around",
                )
            ],
        ),
    )

    result = ingest(
        repo,
        store,
        settings,
        source_id,
        item("2401.00032"),
        pdf_bytes(LOREM),
        extractors={"pdf": dirty, "html": DEFAULT_EXTRACTORS["html"]},
    )

    assert result.outcome is Outcome.INGESTED
    with session_factory() as session:
        section = session.scalars(select(Section)).one()
    assert section.heading == "Heading"
    assert chr(0) not in section.text


def test_exact_and_near_duplicates_are_marked_not_deleted(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    source_id: Any,
    session_factory: sessionmaker[Session],
) -> None:
    body = " ".join(f"alpha{i} beta{i} gamma{i}" for i in range(120))
    ingest(repo, store, settings, source_id, item("2401.00040"), pdf_bytes(body[:2000]))
    ingest(repo, store, settings, source_id, item("2401.00041"), pdf_bytes(body[:2000]))
    ingest(
        repo,
        store,
        settings,
        source_id,
        item("2401.00042"),
        pdf_bytes(body[:2000].replace("alpha30", "changed")),
    )

    with session_factory() as session:
        versions = session.scalars(
            select(DocumentVersion).order_by(DocumentVersion.created_at)
        ).all()
    assert len(versions) == 3
    assert versions[0].duplicate_of is None
    assert versions[1].duplicate_of == versions[0].id
    assert versions[2].duplicate_of == versions[0].id


def test_owner_id_is_stored_and_isolates_identical_bytes(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    source_id: Any,
    session_factory: sessionmaker[Session],
) -> None:
    import uuid

    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()
    content = pdf_bytes(LOREM)
    ingest(repo, store, settings, source_id, item("mine"), content, owner_id=owner_a)
    ingest(repo, store, settings, source_id, item("mine"), content, owner_id=owner_b)
    ingest(repo, store, settings, source_id, item("mine"), content)

    with session_factory() as session:
        owners = session.scalars(select(RawArtifact.owner_id)).all()
    assert len(owners) == 3
    assert set(owners) == {owner_a, owner_b, None}
    assert count(session_factory, Document) == 3


def test_default_isolated_extractor_ingests_a_real_pdf(
    repo: Repository,
    store: LocalBlobStore,
    settings: PipelineSettings,
    source_id: Any,
) -> None:
    result = ingest(
        repo,
        store,
        settings,
        source_id,
        item("2401.00050"),
        pdf_bytes(LOREM),
        extractors=DEFAULT_EXTRACTORS,
    )
    assert result.outcome is Outcome.INGESTED
