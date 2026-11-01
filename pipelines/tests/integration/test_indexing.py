"""Indexing against real PostgreSQL and Qdrant; the models are the fakes of explorai_ai.

Needs DATABASE_URL (superuser, a temporary database is migrated) and QDRANT_URL
(QDRANT_API_KEY when the server requires one). Every test uses its own alias and
embedding model name, so its collections are its own and are dropped afterwards.
"""

import os
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import DeleteAlias, DeleteAliasOperation
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from explorai_ai.chunking import ChunkText
from explorai_ai.config import RagSettings
from explorai_ai.errors import ProviderUnavailable
from explorai_ai.fakes import FakeEmbedder
from explorai_ai.index import QdrantIndex
from explorai_ai.scope import PUBLIC_OWNER, build_scope_filter
from explorai_ai.sparse import encode_query
from explorai_ai.store import ChunkStore
from explorai_db.models import (
    Chunk,
    ChunkIndexEntry,
    Document,
    DocumentVersion,
    RawArtifact,
    Section,
    Source,
    User,
)
from explorai_db.rls import ScopedSessions
from explorai_pipelines.indexing import INDEX_LOCK, index_pending, prune_index
from explorai_pipelines.repository import Repository

pytestmark = pytest.mark.integration

DIM = 16
T0 = datetime(2026, 1, 1, tzinfo=UTC)
RETRIEVAL = "Dense retrieval and sparse retrieval complement each other in practice."
RERANKING = "A cross encoder reranks the fused candidates before generation starts."
CHUNKING = "Overlapping windows keep a sentence cut at an edge retrievable later."


def at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


def fresh_model() -> str:
    return f"t{uuid.uuid4().hex[:8]}"


def rag_settings(model: str, **overrides: Any) -> RagSettings:
    values: dict[str, Any] = {
        "provider": "fake",
        "embedding_model": model,
        "embedding_dimension": DIM,
        "embed_batch_size": 64,
    }
    return RagSettings(**(values | overrides))


class FailingEmbedder(FakeEmbedder):
    """Fails with a provider outage from the ``fail_from``-th call on."""

    def __init__(self, fail_from: int, dimension: int = DIM) -> None:
        super().__init__(dimension=dimension)
        self.calls = 0
        self._fail_from = fail_from

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls >= self._fail_from:
            raise ProviderUnavailable("embedding provider down")
        return super().embed_documents(texts)


@pytest.fixture
def client() -> Iterator[QdrantClient]:
    url = os.environ.get("QDRANT_URL")
    if not url:
        pytest.fail("QDRANT_URL must be set for integration tests")
    qdrant = QdrantClient(url=url, api_key=os.environ.get("QDRANT_API_KEY"))
    yield qdrant
    qdrant.close()


@pytest.fixture
def index(client: QdrantClient) -> Iterator[QdrantIndex]:
    """An index on a unique alias; every collection the test creates is dropped."""
    alias = f"test_alias_{uuid.uuid4().hex[:8]}"
    idx = QdrantIndex(client, alias=alias)
    before = {c.name for c in client.get_collections().collections}
    yield idx
    if idx.alias_target() is not None:
        client.update_collection_aliases(
            change_aliases_operations=[
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=alias))
            ]
        )
    for c in client.get_collections().collections:
        if c.name not in before and c.name.startswith("chunks_t"):
            client.delete_collection(c.name)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder(dimension=DIM)


class Corpus:
    """Rows as the ingestion pipeline leaves them, inserted as the table owner."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory
        self._owners: dict[uuid.UUID, uuid.UUID | None] = {}
        with factory.begin() as session:
            source = Source(kind="test", name=f"index-{uuid.uuid4()}", config={})
            session.add(source)
            session.flush()
            self._source = source.id

    def user(self) -> uuid.UUID:
        with self._factory.begin() as session:
            user = User(email=f"{uuid.uuid4().hex}@example.org", password_hash="x")
            session.add(user)
            session.flush()
            return user.id

    def document(self, owner: uuid.UUID | None = None) -> uuid.UUID:
        with self._factory.begin() as session:
            document = Document(
                source_id=self._source,
                external_id=uuid.uuid4().hex,
                owner_id=owner,
                title="A title",
            )
            session.add(document)
            session.flush()
            self._owners[document.id] = owner
            return document.id

    def version(
        self,
        document_id: uuid.UUID,
        created_at: datetime,
        sections: Sequence[str] = (RETRIEVAL,),
        *,
        status: str = "ok",
        duplicate_of: uuid.UUID | None = None,
    ) -> uuid.UUID:
        key = uuid.uuid4().hex
        with self._factory.begin() as session:
            artifact = RawArtifact(
                source_id=self._source,
                external_id=key,
                url=f"https://example.org/{key}",
                sha256=key.ljust(64, "0"),
                storage_key=key.ljust(64, "0"),
                owner_id=self._owners[document_id],
            )
            session.add(artifact)
            session.flush()
            version = DocumentVersion(
                document_id=document_id,
                artifact_id=artifact.id,
                extractor_version="pdf-1",
                status=status,
                duplicate_of=duplicate_of,
                created_at=created_at,
            )
            session.add(version)
            session.flush()
            session.add_all(
                Section(version_id=version.id, ordinal=i, heading=f"S{i}", text=body)
                for i, body in enumerate(sections)
            )
            return version.id

    def chunk(self, version_id: uuid.UUID, ordinal: int) -> uuid.UUID:
        """A chunk row inserted directly (the parity test chunks every version)."""
        with self._factory.begin() as session:
            chunk = Chunk(
                version_id=version_id,
                ordinal=ordinal,
                heading=None,
                text=f"chunk {ordinal} of {version_id}",
                word_count=4,
                text_hash="f" * 64,
            )
            session.add(chunk)
            session.flush()
            return chunk.id


@pytest.fixture
def corpus(session_factory: sessionmaker[Session]) -> Corpus:
    return Corpus(session_factory)


def chunk_ids_of(
    factory: sessionmaker[Session], version_id: uuid.UUID
) -> set[uuid.UUID]:
    with factory() as session:
        return set(
            session.scalars(select(Chunk.id).where(Chunk.version_id == version_id))
        )


def entry_count(factory: sessionmaker[Session], index_name: str) -> int:
    with factory() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(ChunkIndexEntry)
                .where(ChunkIndexEntry.index_name == index_name)
            )
            or 0
        )


def points(index: QdrantIndex, collection: str) -> set[uuid.UUID]:
    return set(index.point_ids(collection))


def search_ids(
    index: QdrantIndex, embedder: FakeEmbedder, query: str
) -> set[uuid.UUID]:
    hits = index.search(
        dense=embedder.embed_query(query),
        sparse=encode_query(query),
        flt=build_scope_filter(uuid.uuid4(), "all"),
        dense_limit=50,
        sparse_limit=50,
        limit=50,
    )
    return {hit.id for hit in hits}


def test_new_version_is_chunked_embedded_and_indexed(
    repo: Repository,
    index: QdrantIndex,
    embedder: FakeEmbedder,
    corpus: Corpus,
    session_factory: sessionmaker[Session],
    client: QdrantClient,
) -> None:
    user = corpus.user()
    public = corpus.version(corpus.document(), at(0), [RETRIEVAL, RERANKING])
    private_doc = corpus.document(user)
    private = corpus.version(private_doc, at(0), [CHUNKING])
    settings = rag_settings(fresh_model())

    counters = index_pending(repo, embedder, index, settings)

    assert counters == {
        "chunked": 2,
        "embedded": 3,
        "skipped": 0,
        "embedding_failed": 0,
        "reset_entries": 0,
    }
    collection = settings.index_name
    public_chunks = chunk_ids_of(session_factory, public)
    private_chunks = chunk_ids_of(session_factory, private)
    assert len(public_chunks) == 2
    assert len(private_chunks) == 1
    assert points(index, collection) == public_chunks | private_chunks
    assert entry_count(session_factory, settings.index_name) == 3
    assert index.alias_target() == collection
    assert public_chunks <= search_ids(index, embedder, "dense sparse retrieval")
    [record] = client.retrieve(collection, [str(next(iter(private_chunks)))])
    assert record.payload == {
        "owner_id": str(user),
        "document_id": str(private_doc),
        "version_id": str(private),
    }
    [record] = client.retrieve(collection, [str(next(iter(public_chunks)))])
    assert record.payload is not None
    assert record.payload["owner_id"] == PUBLIC_OWNER
    with session_factory() as session:
        chunk = session.scalars(select(Chunk).where(Chunk.version_id == private)).one()
        assert (chunk.heading, chunk.text, chunk.word_count) == (
            "S0",
            CHUNKING,
            len(CHUNKING.split()),
        )
        assert len(chunk.text_hash) == 64


def test_second_pass_changes_nothing(
    repo: Repository,
    index: QdrantIndex,
    embedder: FakeEmbedder,
    corpus: Corpus,
    session_factory: sessionmaker[Session],
) -> None:
    version = corpus.version(corpus.document(), at(0), [RETRIEVAL, RERANKING])
    settings = rag_settings(fresh_model())
    index_pending(repo, embedder, index, settings)
    collection = settings.index_name
    chunks, indexed = chunk_ids_of(session_factory, version), points(index, collection)

    counters = index_pending(repo, embedder, index, settings)

    assert counters == {
        "chunked": 0,
        "embedded": 0,
        "skipped": 0,
        "embedding_failed": 0,
        "reset_entries": 0,
    }
    assert chunk_ids_of(session_factory, version) == chunks
    assert points(index, collection) == indexed
    assert entry_count(session_factory, settings.index_name) == len(chunks)


def test_new_version_replaces_the_old_in_search_eligibility(
    repo: Repository,
    index: QdrantIndex,
    embedder: FakeEmbedder,
    corpus: Corpus,
    session_factory: sessionmaker[Session],
) -> None:
    document = corpus.document()
    old = corpus.version(document, at(0), [RETRIEVAL])
    settings = rag_settings(fresh_model())
    index_pending(repo, embedder, index, settings)
    new = corpus.version(document, at(1), [RETRIEVAL + " Revised edition."])

    counters = index_pending(repo, embedder, index, settings)

    assert counters["chunked"] == 1
    assert counters["embedded"] == 1
    old_chunks = chunk_ids_of(session_factory, old)
    new_chunks = chunk_ids_of(session_factory, new)
    assert repo.eligible_chunk_ids() == new_chunks
    assert repo.pending_chunks(settings.index_name, 10) == []
    # Before pruning both points match; the store re-reads only the new one.
    found = search_ids(index, embedder, "dense sparse retrieval")
    assert old_chunks | new_chunks <= found
    store = ChunkStore(ScopedSessions(session_factory))
    hydrated = store.hydrate(corpus.user(), list(found))
    assert {record.chunk_id for record in hydrated} == new_chunks


def test_duplicate_version_is_not_indexed(
    repo: Repository,
    index: QdrantIndex,
    embedder: FakeEmbedder,
    corpus: Corpus,
    session_factory: sessionmaker[Session],
) -> None:
    original_doc = corpus.document()
    original = corpus.version(original_doc, at(0), [RETRIEVAL])
    copy = corpus.version(corpus.document(), at(1), [RETRIEVAL], duplicate_of=original)
    settings = rag_settings(fresh_model())

    counters = index_pending(repo, embedder, index, settings)

    assert counters["chunked"] == 1
    assert chunk_ids_of(session_factory, copy) == set()
    with session_factory() as session:
        assert session.get(DocumentVersion, copy).chunked_at is None  # type: ignore[union-attr]
    collection = settings.index_name
    assert points(index, collection) == chunk_ids_of(session_factory, original)


def test_zero_chunk_version_is_marked_chunked_once(
    repo: Repository,
    index: QdrantIndex,
    embedder: FakeEmbedder,
    corpus: Corpus,
    session_factory: sessionmaker[Session],
) -> None:
    version = corpus.version(corpus.document(), at(0), ["Too short.", "Also short."])
    settings = rag_settings(fresh_model())

    first = index_pending(repo, embedder, index, settings)
    second = index_pending(repo, embedder, index, settings)

    assert (first["chunked"], first["embedded"]) == (1, 0)
    assert second["chunked"] == 0
    assert chunk_ids_of(session_factory, version) == set()
    with session_factory() as session:
        assert session.get(DocumentVersion, version).chunked_at is not None  # type: ignore[union-attr]
    assert repo.versions_to_chunk(10) == []


def test_saving_chunks_of_an_already_chunked_version_is_a_no_op(
    repo: Repository, corpus: Corpus, session_factory: sessionmaker[Session]
) -> None:
    version = corpus.version(corpus.document(), at(0), [RETRIEVAL])
    [pending] = repo.versions_to_chunk(10)
    assert pending.version_id == version
    assert [s.text for s in pending.sections] == [RETRIEVAL]
    chunk = ChunkText(0, "S0", RETRIEVAL, len(RETRIEVAL.split()))
    repo.save_chunks(version, [chunk])
    repo.save_chunks(version, [chunk])

    assert len(chunk_ids_of(session_factory, version)) == 1


def test_prune_removes_points_of_stale_versions_and_deleted_documents(
    repo: Repository,
    index: QdrantIndex,
    embedder: FakeEmbedder,
    corpus: Corpus,
    session_factory: sessionmaker[Session],
) -> None:
    kept_doc, deleted_doc = corpus.document(), corpus.document()
    old = corpus.version(kept_doc, at(0), [RETRIEVAL])
    gone = corpus.version(deleted_doc, at(0), [RERANKING, CHUNKING])
    settings = rag_settings(fresh_model())
    index_pending(repo, embedder, index, settings)
    new = corpus.version(kept_doc, at(1), [CHUNKING])
    index_pending(repo, embedder, index, settings)
    stale = chunk_ids_of(session_factory, old) | chunk_ids_of(session_factory, gone)
    with session_factory.begin() as session:
        session.execute(
            text("DELETE FROM documents WHERE id = :id"), {"id": deleted_doc}
        )
    collection = settings.index_name
    assert stale <= points(index, collection)

    counters = prune_index(repo, index, settings)

    assert counters == {"pruned": len(stale), "skipped": 0}
    assert points(index, collection) == chunk_ids_of(session_factory, new)
    assert prune_index(repo, index, settings) == {"pruned": 0, "skipped": 0}


def test_model_change_builds_a_new_collection_and_switches_the_alias_only_when_complete(
    repo: Repository,
    index: QdrantIndex,
    embedder: FakeEmbedder,
    corpus: Corpus,
    client: QdrantClient,
) -> None:
    for body in (RETRIEVAL, RERANKING, CHUNKING):
        corpus.version(corpus.document(), at(0), [body])
    old_settings = rag_settings(fresh_model())
    index_pending(repo, embedder, index, old_settings)
    old_collection = old_settings.index_name
    everything = points(index, old_collection)
    assert len(everything) == 3

    new_settings = rag_settings(fresh_model(), embed_batch_size=1)
    new_collection = new_settings.index_name
    stopped = index_pending(repo, FailingEmbedder(fail_from=2), index, new_settings)

    assert stopped["embedded"] == 1
    assert stopped["embedding_failed"] == 1
    assert len(points(index, new_collection)) == 1
    assert index.alias_target() == old_collection
    # Searches still see the complete old collection, not the partial new one.
    assert search_ids(index, embedder, "retrieval reranks windows") == everything

    finished = index_pending(repo, embedder, index, new_settings)

    assert finished["embedded"] == 2
    assert index.alias_target() == new_collection
    # The old collection is no longer needed: searches are served by the new one.
    client.delete_collection(old_collection)
    assert search_ids(index, embedder, "retrieval reranks windows") == everything


def test_embedding_failure_midway_resumes_next_pass(
    repo: Repository,
    index: QdrantIndex,
    embedder: FakeEmbedder,
    corpus: Corpus,
    session_factory: sessionmaker[Session],
) -> None:
    for body in (RETRIEVAL, RERANKING, CHUNKING):
        corpus.version(corpus.document(), at(0), [body])
    settings = rag_settings(fresh_model(), embed_batch_size=1)
    collection = settings.index_name

    first = index_pending(repo, FailingEmbedder(fail_from=2), index, settings)

    assert (first["chunked"], first["embedded"], first["embedding_failed"]) == (3, 1, 1)
    assert entry_count(session_factory, settings.index_name) == 1
    assert len(points(index, collection)) == 1
    assert len(repo.pending_chunks(settings.index_name, 10)) == 2
    assert index.alias_target() is None

    second = index_pending(repo, embedder, index, settings)

    assert (second["chunked"], second["embedded"], second["embedding_failed"]) == (
        0,
        2,
        0,
    )
    assert entry_count(session_factory, settings.index_name) == 3
    assert points(index, collection) == repo.eligible_chunk_ids()
    assert index.alias_target() == collection


def test_index_pass_is_skipped_while_another_holds_the_lock(
    repo: Repository, index: QdrantIndex, embedder: FakeEmbedder, corpus: Corpus
) -> None:
    corpus.version(corpus.document(), at(0), [RETRIEVAL])
    settings = rag_settings(fresh_model())

    with repo.advisory_lock(INDEX_LOCK) as held:
        assert held is True
        counters = index_pending(repo, embedder, index, settings)

    assert counters["skipped"] == 1
    assert repo.versions_to_chunk(10) != []


def test_pipeline_and_chunk_store_agree_on_eligible_chunks(
    repo: Repository, corpus: Corpus, session_factory: sessionmaker[Session]
) -> None:
    """The two sides of the eligibility rule select exactly the same chunks."""
    user_a, user_b, outsider = corpus.user(), corpus.user(), corpus.user()
    paper = corpus.document(user_a)
    older = corpus.version(paper, at(0))
    current = corpus.version(paper, at(1))
    failed = corpus.version(paper, at(2), status="failed")
    copy = corpus.document(user_a)
    copy_old = corpus.version(copy, at(0))
    copy_dup = corpus.version(copy, at(1), duplicate_of=current)
    theirs = corpus.version(corpus.document(user_b), at(0))
    public = corpus.version(corpus.document(), at(0))
    tie_doc = corpus.document()
    tie_a = corpus.version(tie_doc, at(3))
    tie_b = corpus.version(tie_doc, at(3))
    chunks = {
        version: {corpus.chunk(version, 0), corpus.chunk(version, 1)}
        for version in (older, current, failed, copy_old, copy_dup, theirs, public)
    }
    tie_chunks = {tie_a: {corpus.chunk(tie_a, 0)}, tie_b: {corpus.chunk(tie_b, 0)}}
    every_id = [c for ids in (*chunks.values(), *tie_chunks.values()) for c in ids]
    store = ChunkStore(ScopedSessions(session_factory))

    seen_by_store = {
        record.chunk_id
        for user in (user_a, user_b, outsider)
        for record in store.hydrate(user, every_id)
    }

    # Equal ``created_at``: the larger id is the latest.
    tie_winner = tie_chunks[max(tie_a, tie_b)]
    expected = chunks[current] | chunks[theirs] | chunks[public] | tie_winner
    assert seen_by_store == expected
    assert repo.eligible_chunk_ids() == seen_by_store


def test_dimension_change_builds_a_new_index_and_keeps_the_old_alias_until_complete(
    repo: Repository,
    index: QdrantIndex,
    embedder: FakeEmbedder,
    corpus: Corpus,
    session_factory: sessionmaker[Session],
) -> None:
    for body in (RETRIEVAL, RERANKING, CHUNKING):
        corpus.version(corpus.document(), at(0), [body])
    model = fresh_model()
    old_settings = rag_settings(model)
    index_pending(repo, embedder, index, old_settings)
    everything = repo.eligible_chunk_ids()
    # Same model, another dimension: another index in which every chunk is pending.
    wide = DIM * 2
    new_settings = rag_settings(model, embedding_dimension=wide, embed_batch_size=1)
    assert new_settings.index_name != old_settings.index_name
    pending = repo.pending_chunks(new_settings.index_name, 10)
    assert {chunk.chunk_id for chunk in pending} == everything

    stopped = index_pending(
        repo, FailingEmbedder(fail_from=2, dimension=wide), index, new_settings
    )

    assert stopped["embedded"] == 1
    assert len(points(index, new_settings.index_name)) == 1
    assert index.alias_target() == old_settings.index_name
    assert search_ids(index, embedder, "retrieval reranks windows") == everything

    wide_embedder = FakeEmbedder(dimension=wide)
    finished = index_pending(repo, wide_embedder, index, new_settings)

    assert finished["embedded"] == 2
    assert index.alias_target() == new_settings.index_name
    assert points(index, new_settings.index_name) == everything
    assert search_ids(index, wide_embedder, "retrieval reranks windows") == everything
    # The old index keeps its entries (a switch back needs no re-embedding).
    assert entry_count(session_factory, old_settings.index_name) == 3


def test_lost_collection_is_rebuilt_from_postgres(
    repo: Repository,
    index: QdrantIndex,
    embedder: FakeEmbedder,
    corpus: Corpus,
    session_factory: sessionmaker[Session],
    client: QdrantClient,
) -> None:
    for body in (RETRIEVAL, RERANKING, CHUNKING):
        corpus.version(corpus.document(), at(0), [body])
    settings = rag_settings(fresh_model())
    index_pending(repo, embedder, index, settings)
    everything = repo.eligible_chunk_ids()
    # Qdrant loses the collection (volume reset, manual delete); the entries remain.
    client.delete_collection(settings.index_name)
    assert entry_count(session_factory, settings.index_name) == 3
    assert search_ids(index, embedder, "retrieval reranks windows") == set()

    counters = index_pending(repo, embedder, index, settings)

    assert counters["reset_entries"] == 3
    assert counters["embedded"] == 3
    assert entry_count(session_factory, settings.index_name) == 3
    assert points(index, settings.index_name) == everything
    assert index.alias_target() == settings.index_name
    assert search_ids(index, embedder, "retrieval reranks windows") == everything
    # Rebuilt: the next pass has nothing to reset or embed.
    again = index_pending(repo, embedder, index, settings)
    assert (again["reset_entries"], again["embedded"]) == (0, 0)


def test_indexed_state_follows_the_index_name_across_a_dimension_change(
    repo: Repository,
    index: QdrantIndex,
    embedder: FakeEmbedder,
    corpus: Corpus,
    session_factory: sessionmaker[Session],
) -> None:
    user = corpus.user()
    document = corpus.document(user)
    corpus.version(document, at(0), [RETRIEVAL, RERANKING])
    model = fresh_model()
    old_settings = rag_settings(model)
    new_settings = rag_settings(model, embedding_dimension=DIM * 2)
    store = ChunkStore(ScopedSessions(session_factory))
    index_pending(repo, embedder, index, old_settings)

    assert store.indexed_state(user, [document], old_settings.index_name) == {
        document: True
    }
    # The same model at a new dimension has indexed nothing yet.
    assert store.indexed_state(user, [document], new_settings.index_name) == {
        document: False
    }

    index_pending(repo, FakeEmbedder(dimension=DIM * 2), index, new_settings)

    assert store.indexed_state(user, [document], new_settings.index_name) == {
        document: True
    }
