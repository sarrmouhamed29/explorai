"""One document's journey: raw artifact, then versioned text, then sections.

No Prefect here: flows call these functions. Idempotence rests on the
``(source, external_id, owner, sha256)`` identity of the raw artifact and on
the ``(artifact, extractor_version)`` identity of a version.
"""

import hashlib
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from explorai_db.blobs import BlobStore
from explorai_pipelines.clean import normalize
from explorai_pipelines.config import PipelineSettings
from explorai_pipelines.dedup import NearDuplicateIndex, minhash_signature, text_hash
from explorai_pipelines.extract.html import HTML_EXTRACTOR_VERSION, extract_html
from explorai_pipelines.extract.models import (
    ExtractedDocument,
    ExtractedSection,
    Unextractable,
)
from explorai_pipelines.extract.pdf import PDF_EXTRACTOR_VERSION, extract_pdf_isolated
from explorai_pipelines.netsafe import FetchResult
from explorai_pipelines.repository import Repository
from explorai_pipelines.sources.base import SourceItem

logger = logging.getLogger(__name__)

Kind = Literal["pdf", "html"]


class Outcome(StrEnum):
    UNCHANGED = "unchanged"
    INGESTED = "ingested"
    NEW_VERSION = "new_version"
    UNEXTRACTABLE = "unextractable"
    FAILED = "failed"


@dataclass(frozen=True)
class ItemResult:
    outcome: Outcome
    detail: str | None = None


@dataclass(frozen=True)
class RunReport:
    run_id: uuid.UUID
    status: str
    counters: dict[str, int]
    errors: list[str]


@dataclass(frozen=True)
class Extractor:
    version: str
    kind: Kind
    # (bytes, url, timeout seconds) -> document; raises Unextractable.
    run: Callable[[bytes, str | None, float], ExtractedDocument]


DEFAULT_EXTRACTORS: Mapping[str, Extractor] = {
    "pdf": Extractor(
        version=PDF_EXTRACTOR_VERSION,
        kind="pdf",
        run=lambda data, url, timeout: extract_pdf_isolated(data, timeout),
    ),
    "html": Extractor(
        version=HTML_EXTRACTOR_VERSION,
        kind="html",
        run=lambda data, url, timeout: extract_html(data, url),
    ),
}

BatchWork = Sequence[tuple[SourceItem, Callable[[], FetchResult]]]


def sniff_kind(content: bytes, content_type: str | None, url: str) -> Kind:
    """PDF if the bytes say so (magic number wins over headers and file names)."""
    if content.lstrip()[:5] == b"%PDF-" or content[:4] == b"%PDF":
        return "pdf"
    return "html"


def _clean_sections(document: ExtractedDocument) -> list[ExtractedSection]:
    cleaned = [
        ExtractedSection(
            heading=normalize(s.heading) or None if s.heading else None,
            text=normalize(s.text),
        )
        for s in document.sections
    ]
    return [s for s in cleaned if s.text]


def _process(
    *,
    repo: Repository,
    index: NearDuplicateIndex,
    source_id: uuid.UUID,
    external_id: str,
    owner_id: uuid.UUID | None,
    item_title: str | None,
    item_metadata: dict[str, str] | None,
    artifact_id: uuid.UUID,
    data: bytes,
    url: str | None,
    extractor: Extractor,
    timeout: float,
) -> tuple[Outcome, bool]:
    """Extract, deduplicate and save one version."""
    try:
        document = extractor.run(data, url, timeout)
    except Unextractable as error:
        repo.save_version(
            source_id=source_id,
            external_id=external_id,
            owner_id=owner_id,
            title=item_title,
            extracted_title=None,
            source_metadata=item_metadata,
            artifact_id=artifact_id,
            extractor_version=extractor.version,
            status="unextractable",
            text_hash=None,
            signature=None,
            duplicate_of=None,
            sections=[],
        )
        logger.info("unextractable %s: %s", external_id, error)
        return Outcome.UNEXTRACTABLE, False
    sections = _clean_sections(document)
    full_text = "\n\n".join(s.text for s in sections)
    digest = text_hash(full_text)
    signature = minhash_signature(full_text)
    duplicate: uuid.UUID | None = repo.find_exact_duplicate(
        digest, source_id, external_id, owner_id
    )
    if duplicate is None:
        ignore = repo.document_version_ids(source_id, external_id, owner_id)
        near = index.find(signature, ignore=ignore)
        duplicate = uuid.UUID(near) if near else None
    saved = repo.save_version(
        source_id=source_id,
        external_id=external_id,
        owner_id=owner_id,
        title=item_title,
        extracted_title=document.title,
        source_metadata=item_metadata,
        artifact_id=artifact_id,
        extractor_version=extractor.version,
        status="ok",
        text_hash=digest,
        signature=signature,
        duplicate_of=duplicate,
        sections=sections,
    )
    if duplicate is None:
        # Only canonical versions are indexed, so duplicates never form chains.
        index.add(str(saved.version_id), signature)
    existed = not saved.document_created
    return (Outcome.NEW_VERSION if existed else Outcome.INGESTED), existed


def _index_from_database(
    repo: Repository, owner_id: uuid.UUID | None
) -> NearDuplicateIndex:
    """Near-duplicate index of one owner; never searched across owners."""
    index = NearDuplicateIndex()
    for key, signature in repo.load_signatures(owner_id):
        index.add(key, signature)
    return index


def ingest_item(
    item: SourceItem,
    fetched: FetchResult,
    *,
    repo: Repository,
    store: BlobStore,
    source_id: uuid.UUID,
    settings: PipelineSettings,
    owner_id: uuid.UUID | None = None,
    index: NearDuplicateIndex | None = None,
    extractors: Mapping[str, Extractor] = DEFAULT_EXTRACTORS,
) -> ItemResult:
    """Ingest one downloaded item; never raises, failures become ``Outcome.FAILED``."""
    try:
        sha256 = hashlib.sha256(fetched.content).hexdigest()
        extractor = extractors[
            sniff_kind(fetched.content, fetched.content_type, item.url)
        ]
        artifact_id = repo.find_artifact(source_id, item.external_id, owner_id, sha256)
        if artifact_id is not None and repo.has_version(artifact_id, extractor.version):
            return ItemResult(Outcome.UNCHANGED)
        if artifact_id is None:
            artifact_id = repo.store_artifact(
                source_id=source_id,
                external_id=item.external_id,
                url=fetched.final_url,
                sha256=sha256,
                storage_key=store.put(fetched.content),
                owner_id=owner_id,
            )
        outcome, _ = _process(
            repo=repo,
            index=index if index is not None else _index_from_database(repo, owner_id),
            source_id=source_id,
            external_id=item.external_id,
            owner_id=owner_id,
            item_title=item.title,
            item_metadata=item.metadata,
            artifact_id=artifact_id,
            data=fetched.content,
            url=fetched.final_url,
            extractor=extractor,
            timeout=settings.extract_timeout_seconds,
        )
        return ItemResult(outcome)
    except Exception as error:  # noqa: BLE001 - one bad item must not stop the batch
        logger.warning(
            "ingestion failed for %s: %s", item.external_id, type(error).__name__
        )
        return ItemResult(
            Outcome.FAILED, f"{item.external_id}: {type(error).__name__}: {error}"
        )


def reextract(
    *,
    repo: Repository,
    store: BlobStore,
    settings: PipelineSettings,
    extractors: Mapping[str, Extractor] = DEFAULT_EXTRACTORS,
) -> dict[str, int]:
    """Create versions for stored artifacts lacking one for the current extractors.

    Reads only stored bytes: no network. Returns counters by outcome.
    """
    counters = {o.value: 0 for o in Outcome}
    indexes: dict[uuid.UUID | None, NearDuplicateIndex] = {}
    for extractor in extractors.values():
        for ref in repo.artifacts_missing_version(extractor.version):
            try:
                data = store.get(ref.storage_key)
                if ref.owner_id not in indexes:
                    indexes[ref.owner_id] = _index_from_database(repo, ref.owner_id)
                if sniff_kind(data, None, ref.url) != extractor.kind:
                    continue
                outcome, _ = _process(
                    repo=repo,
                    index=indexes[ref.owner_id],
                    source_id=ref.source_id,
                    external_id=ref.external_id,
                    owner_id=ref.owner_id,
                    item_title=None,
                    item_metadata=None,
                    artifact_id=ref.id,
                    data=data,
                    url=ref.url,
                    extractor=extractor,
                    timeout=settings.extract_timeout_seconds,
                )
                # A re-extraction adds a version to an existing document.
                counters[
                    Outcome.NEW_VERSION.value
                    if outcome is Outcome.INGESTED
                    else outcome.value
                ] += 1
            except Exception as error:  # noqa: BLE001 - keep going, count the failure
                logger.warning(
                    "re-extraction failed for %s: %s", ref.external_id, error
                )
                counters[Outcome.FAILED.value] += 1
    return counters


def run_batch(
    work: BatchWork,
    *,
    repo: Repository,
    store: BlobStore,
    source_id: uuid.UUID,
    settings: PipelineSettings,
    owner_id: uuid.UUID | None = None,
    extractors: Mapping[str, Extractor] = DEFAULT_EXTRACTORS,
) -> RunReport:
    """Ingest items one by one, recording the run. Each item is isolated."""
    run_id = repo.start_run(source_id)
    counters = {o.value: 0 for o in Outcome} | {"recovered": 0}
    errors: list[str] = []
    status = "failed"
    try:
        index = _index_from_database(repo, owner_id)
        for item, fetch in work:
            try:
                fetched = fetch()
            except Exception as error:  # noqa: BLE001 - a failed download is one failed item
                counters[Outcome.FAILED.value] += 1
                errors.append(
                    f"{item.external_id}: fetch failed: {type(error).__name__}: {error}"
                )
                continue
            result = ingest_item(
                item,
                fetched,
                repo=repo,
                store=store,
                source_id=source_id,
                settings=settings,
                owner_id=owner_id,
                index=index,
                extractors=extractors,
            )
            counters[result.outcome.value] += 1
            if result.detail:
                errors.append(result.detail)
        # Finish what earlier runs left half-done (bytes stored, no version yet):
        # an item that is no longer in the source's latest listing would never be
        # retried otherwise.
        caught_up = reextract(
            repo=repo, store=store, settings=settings, extractors=extractors
        )
        counters["recovered"] = caught_up[Outcome.NEW_VERSION.value]
        counters["unextractable"] += caught_up[Outcome.UNEXTRACTABLE.value]
        if caught_up[Outcome.FAILED.value]:
            counters["failed"] += caught_up[Outcome.FAILED.value]
            errors.append(
                f"catch-up: {caught_up[Outcome.FAILED.value]} stored artifact(s) "
                "could not be re-extracted"
            )
        status = "completed_with_errors" if errors else "completed"
    finally:
        repo.finish_run(run_id, counters, errors, status)
    return RunReport(run_id=run_id, status=status, counters=counters, errors=errors)
