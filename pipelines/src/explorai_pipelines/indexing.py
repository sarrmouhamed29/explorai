"""From canonical versions to searchable points: chunk, embed, upsert, record.

The vector index is derived data. PostgreSQL says what should be searchable (the
eligibility rule of ``explorai_ai.store``) and what is already indexed
(``chunk_index_entries``); Qdrant only holds vectors under the chunk ids. So a
pass is idempotent (a point id is the chunk id, an upsert overwrites, an entry is
recorded once), an interrupted pass resumes where it stopped, and the whole index
can be rebuilt from the database. Recording an entry *after* the upsert means a
crash in between re-embeds a few chunks next time, never loses one.

A stale point (older version, duplicate, deleted document) is harmless until
``prune_index`` removes it: the API re-reads every hit from PostgreSQL under
Row-Level Security and the same eligibility rule, so it is silently dropped.

No Prefect here: the flows in ``flows/indexing.py`` call these functions.
"""

import logging
import uuid
from collections.abc import Iterable, Iterator, Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from explorai_ai.chunking import ChunkText, chunk_sections, embedding_text
from explorai_ai.config import RagSettings
from explorai_ai.errors import ProviderError
from explorai_ai.index import IndexPoint
from explorai_ai.providers.base import Embedder
from explorai_ai.scope import PUBLIC_OWNER
from explorai_ai.sparse import encode_document
from explorai_pipelines.repository import PendingChunk, VersionToChunk

logger = logging.getLogger(__name__)

# Advisory lock keys, next to CATCH_UP_LOCK = 4242001 (ingest.py).
INDEX_LOCK = 4242002
PRUNE_LOCK = 4242003
PRUNE_BATCH = 500


class IndexingRepository(Protocol):
    """What indexing needs from ``Repository`` (unit tests pass a fake)."""

    def advisory_lock(self, key: int) -> AbstractContextManager[bool]: ...

    def versions_to_chunk(self, limit: int) -> list[VersionToChunk]: ...

    def save_chunks(
        self, version_id: uuid.UUID, chunks: Sequence[ChunkText]
    ) -> None: ...

    def pending_chunks(self, index_name: str, limit: int) -> list[PendingChunk]: ...

    def record_entries(
        self, chunk_ids: Sequence[uuid.UUID], index_name: str
    ) -> None: ...

    def clear_entries(self, index_name: str) -> int: ...

    def eligible_chunk_ids(self) -> set[uuid.UUID]: ...


class VectorIndex(Protocol):
    """What indexing needs from ``QdrantIndex`` (unit tests pass a fake)."""

    def collection_exists(self, name: str) -> bool: ...

    def ensure_collection(self, model: str, dimension: int) -> str: ...

    def upsert(self, collection: str, points: Sequence[IndexPoint]) -> None: ...

    def delete(self, collection: str, ids: Sequence[uuid.UUID]) -> None: ...

    def alias_target(self) -> str | None: ...

    def switch_alias(self, collection: str) -> None: ...

    def point_ids(self, collection: str) -> Iterator[uuid.UUID]: ...


def owner_value(owner_id: uuid.UUID | None) -> str:
    """The ``owner_id`` payload of a point: the user id, or the public marker."""
    return PUBLIC_OWNER if owner_id is None else str(owner_id)


def chunk_pending_versions(
    repo: IndexingRepository, settings: RagSettings, *, limit: int = 200
) -> int:
    """Cut up to ``limit`` eligible, not yet chunked versions; return how many.

    Local work only (no provider). A version whose sections are all too short
    gets zero chunks and is still marked chunked.
    """
    versions = repo.versions_to_chunk(limit)
    for version in versions:
        chunks = chunk_sections(
            version.sections,
            target_words=settings.chunk_words,
            overlap_words=settings.chunk_overlap_words,
            max_chunks=settings.max_chunks_per_document,
        )
        repo.save_chunks(version.version_id, chunks)
    return len(versions)


def _points(
    chunks: Sequence[PendingChunk], dense: list[list[float]]
) -> list[IndexPoint]:
    return [
        IndexPoint(
            id=chunk.chunk_id,
            dense=vector,
            sparse=encode_document(embedding_text(chunk.heading, chunk.text)),
            owner=owner_value(chunk.owner_id),
            document_id=chunk.document_id,
            version_id=chunk.version_id,
        )
        for chunk, vector in zip(chunks, dense, strict=True)
    ]


def index_pending(
    repo: IndexingRepository,
    embedder: Embedder,
    index: VectorIndex,
    settings: RagSettings,
) -> dict[str, int]:
    """One indexing pass under ``INDEX_LOCK``; counters by step.

    ``chunked`` versions, ``embedded`` chunks, ``skipped`` (1 when another pass
    holds the lock: nothing is done), ``embedding_failed`` (1 when a provider
    error stopped the pass: what was embedded is kept, the rest stays pending for
    the next pass; this function never raises for a provider outage),
    ``reset_entries`` (entries forgotten because their collection was missing).

    The index is the collection of the configured model *and* dimension, and the
    entries are keyed by its name (``settings.index_name``): a new model or a new
    dimension is a new index in which every chunk is pending. The alias moves to
    it only after a pass that leaves no chunk pending for it: during the change,
    searches keep using the complete old collection until the new one has caught
    up, then switch in one atomic operation.

    If the collection does not exist while entries for it do (Qdrant data lost,
    collection deleted by hand), the entries are cleared *before* the collection
    is recreated, so the pass rebuilds it from PostgreSQL instead of leaving an
    empty collection that the entries claim is full.
    """
    counters = {
        "chunked": 0,
        "embedded": 0,
        "skipped": 0,
        "embedding_failed": 0,
        "reset_entries": 0,
    }
    model = settings.index_model
    name = settings.index_name
    if embedder.dimension != settings.embedding_dimension:
        raise ValueError("embedder dimension does not match RAG_EMBEDDING_DIMENSION")
    with repo.advisory_lock(INDEX_LOCK) as acquired:
        if not acquired:
            counters["skipped"] = 1
            return counters
        counters["chunked"] = chunk_pending_versions(repo, settings)
        if not index.collection_exists(name):
            counters["reset_entries"] = repo.clear_entries(name)
            if counters["reset_entries"]:
                logger.warning(
                    "collection %s missing: %d entries cleared, rebuilding",
                    name,
                    counters["reset_entries"],
                )
        # Same name: both come from QdrantIndex.collection_name over index_model
        # (the fake provider's index is distinct from the real model's).
        collection = index.ensure_collection(model, settings.embedding_dimension)
        if collection != name:
            raise RuntimeError("collection name differs from settings.index_name")
        done: set[uuid.UUID] = set()
        while batch := repo.pending_chunks(name, settings.embed_batch_size):
            if any(chunk.chunk_id in done for chunk in batch):
                # Recorded chunks are never pending again; seeing one twice means
                # the entries are not being written. Stop rather than loop forever.
                raise RuntimeError("indexed chunks are still reported as pending")
            texts = [embedding_text(chunk.heading, chunk.text) for chunk in batch]
            try:
                dense = embedder.embed_documents(texts)
            except ProviderError as error:
                # Only the error type is logged: never chunk text.
                logger.warning(
                    "embedding stopped after %d chunks: %s",
                    counters["embedded"],
                    type(error).__name__,
                )
                counters["embedding_failed"] = 1
                return counters
            index.upsert(collection, _points(batch, dense))
            ids = [chunk.chunk_id for chunk in batch]
            repo.record_entries(ids, name)
            done.update(ids)
            counters["embedded"] += len(batch)
        if index.alias_target() != collection:
            index.switch_alias(collection)
            logger.info("alias now points at %s", collection)
        return counters


def _batches(ids: Iterable[uuid.UUID], size: int) -> Iterator[list[uuid.UUID]]:
    batch: list[uuid.UUID] = []
    for item in ids:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def prune_index(
    repo: IndexingRepository, index: VectorIndex, settings: RagSettings
) -> dict[str, int]:
    """Delete the points of the live collection that should not be searchable.

    Under ``PRUNE_LOCK``; ``skipped`` is 1 when another prune holds it. Only the
    collection behind the alias is pruned (a collection being filled for a new
    model is pruned once it goes live). With no alias yet there is nothing to
    prune.

    The point ids are read *before* the eligible chunk ids. A chunk row is always
    committed before its point is upserted, so every point listed here either
    has an eligible chunk in the set read afterwards or is truly stale; a point
    upserted during the prune is not in the listing and cannot be deleted by it.
    ``settings`` is accepted for symmetry with ``index_pending`` and unused.
    """
    del settings
    counters = {"pruned": 0, "skipped": 0}
    with repo.advisory_lock(PRUNE_LOCK) as acquired:
        if not acquired:
            counters["skipped"] = 1
            return counters
        collection = index.alias_target()
        if collection is None:
            return counters
        listed = list(index.point_ids(collection))
        eligible = repo.eligible_chunk_ids()
        stale = (point_id for point_id in listed if point_id not in eligible)
        for batch in _batches(stale, PRUNE_BATCH):
            index.delete(collection, batch)
            counters["pruned"] += len(batch)
        return counters
