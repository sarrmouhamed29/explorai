"""Retriever against a real PostgreSQL (RLS) and a real Qdrant, with fake models.

Rows are inserted as the table owner (who bypasses RLS) and their points are
upserted straight into Qdrant, standing in for the indexing pipeline; the
retriever reads only through ``ChunkStore`` (``ScopedSessions``) as the API will.
"""

import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import DeleteAlias, DeleteAliasOperation
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from explorai_ai.config import RagSettings
from explorai_ai.errors import ProviderError, ProviderUnavailable, RetrievalUnavailable
from explorai_ai.fakes import FakeEmbedder, FakeGenerator, FakeReranker
from explorai_ai.index import IndexPoint, QdrantIndex
from explorai_ai.models import RerankHit
from explorai_ai.retriever import RetrievalResult, Retriever
from explorai_ai.scope import PUBLIC_OWNER, Scope, build_scope_filter
from explorai_ai.sparse import encode_document, encode_query
from explorai_ai.store import ChunkStore
from explorai_db.models import (
    Chunk,
    Document,
    DocumentVersion,
    RawArtifact,
    Source,
    User,
)
from explorai_db.rls import ScopedSessions
from explorai_db.session import make_engine, make_session_factory

pytestmark = pytest.mark.integration

DIM = 64
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def factory(database_url: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE users, documents, raw_artifacts, document_versions, "
                "sections, chunks, chunk_index_entries CASCADE"
            )
        )
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def index(client: QdrantClient) -> Iterator[QdrantIndex]:
    """A live collection behind a unique alias, both dropped after the test."""
    alias = f"test_retriever_{uuid.uuid4().hex[:8]}"
    idx = QdrantIndex(client, alias=alias)
    collection = idx.ensure_collection(f"t{uuid.uuid4().hex[:8]}", DIM)
    idx.switch_alias(collection)
    yield idx
    client.update_collection_aliases(
        change_aliases_operations=[
            DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=alias))
        ]
    )
    client.delete_collection(collection)


class World:
    """Inserts rows as the table owner and indexes their chunks directly."""

    def __init__(self, factory: sessionmaker[Session], index: QdrantIndex) -> None:
        self._factory = factory
        self._index = index
        self._collection = index.alias_target()
        assert self._collection is not None
        self._owners: dict[uuid.UUID, uuid.UUID | None] = {}
        with factory.begin() as session:
            source = Source(kind="test", name=f"retriever-{uuid.uuid4()}", config={})
            session.add(source)
            session.flush()
            self._source = source.id

    def user(self) -> uuid.UUID:
        with self._factory.begin() as session:
            user = User(email=f"{uuid.uuid4().hex}@example.org", password_hash="x")
            session.add(user)
            session.flush()
            return user.id

    def document(self, owner: uuid.UUID | None, title: str = "Paper") -> uuid.UUID:
        with self._factory.begin() as session:
            document = Document(
                source_id=self._source,
                external_id=uuid.uuid4().hex,
                owner_id=owner,
                title=title,
            )
            session.add(document)
            session.flush()
            self._owners[document.id] = owner
            return document.id

    def version(self, document_id: uuid.UUID, hours: int = 0) -> uuid.UUID:
        key = uuid.uuid4().hex
        created_at = T0 + timedelta(hours=hours)
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
                status="ok",
                created_at=created_at,
                chunked_at=created_at,
            )
            session.add(version)
            session.flush()
            return version.id

    def chunk(
        self,
        version_id: uuid.UUID,
        body: str,
        *,
        ordinal: int = 0,
        payload_owner: str | None = None,
    ) -> uuid.UUID:
        """Insert a chunk and upsert its point; ``payload_owner`` forges the owner."""
        with self._factory.begin() as session:
            version = session.get(DocumentVersion, version_id)
            assert version is not None
            document_id = version.document_id
            chunk = Chunk(
                version_id=version_id,
                ordinal=ordinal,
                heading="Section",
                text=body,
                word_count=len(body.split()),
                text_hash="f" * 64,
            )
            session.add(chunk)
            session.flush()
            chunk_id = chunk.id
        owner = self._owners[document_id]
        assert self._collection is not None
        self._index.upsert(
            self._collection,
            [
                IndexPoint(
                    id=chunk_id,
                    dense=FakeEmbedder(DIM).embed_query(body),
                    sparse=encode_document(body),
                    owner=payload_owner or (str(owner) if owner else PUBLIC_OWNER),
                    document_id=document_id,
                    version_id=version_id,
                )
            ],
        )
        return chunk_id

    def document_with_chunk(
        self, owner: uuid.UUID | None, body: str
    ) -> tuple[uuid.UUID, uuid.UUID]:
        document = self.document(owner)
        return document, self.chunk(self.version(document), body)


@pytest.fixture
def world(factory: sessionmaker[Session], index: QdrantIndex) -> World:
    return World(factory, index)


@dataclass
class RecordingEmbedder:
    texts: list[str] = field(default_factory=list)
    model: str = "fake"
    dimension: int = DIM

    def embed_query(self, text: str) -> list[float]:
        self.texts.append(text)
        return FakeEmbedder(DIM).embed_query(text)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


class FailingRewrites(FakeGenerator):
    def rewrite_queries(self, question: str, max_queries: int) -> list[str]:
        raise ProviderUnavailable("rewrite timed out")


class FailingReranker:
    def rerank(
        self, query: str, documents: Sequence[str], top_k: int
    ) -> list[RerankHit]:
        raise ProviderError("rerank failed")


def settings(**overrides: object) -> RagSettings:
    values: dict[str, object] = {"provider": "fake", "embedding_dimension": DIM}
    values.update(overrides)
    return RagSettings(**values)  # type: ignore[arg-type]


def retriever(
    index: QdrantIndex,
    factory: sessionmaker[Session],
    *,
    embedder: RecordingEmbedder | None = None,
    reranker: FakeReranker | FailingReranker | None = None,
    generator: FakeGenerator | None = None,
    **overrides: object,
) -> Retriever:
    return Retriever(
        index=index,
        embedder=embedder or RecordingEmbedder(),
        reranker=reranker or FakeReranker(),
        generator=generator or FakeGenerator(),
        store=ChunkStore(ScopedSessions(factory)),
        settings=settings(**overrides),
    )


def chunk_ids(result: RetrievalResult) -> list[uuid.UUID]:
    return [c.chunk_id for c in result.candidates]


def test_rewrite_failure_falls_back_to_the_question(
    world: World, index: QdrantIndex, factory: sessionmaker[Session]
) -> None:
    user = world.user()
    _, chunk = world.document_with_chunk(
        user, "Self-attention replaces recurrence in the transformer."
    )
    embedder = RecordingEmbedder()
    question = "Does self-attention replace recurrence?"
    result = retriever(
        index, factory, embedder=embedder, generator=FailingRewrites()
    ).retrieve(user, question)
    assert embedder.texts == [question]
    assert chunk_ids(result) == [chunk]
    candidate = result.candidates[0]
    assert candidate.text == "Self-attention replaces recurrence in the transformer."
    assert candidate.document_title == "Paper"
    assert candidate.heading == "Section"


def test_rewrites_are_searched_too(
    world: World, index: QdrantIndex, factory: sessionmaker[Session]
) -> None:
    user = world.user()
    world.document_with_chunk(user, "Attention weights come from a softmax.")
    embedder = RecordingEmbedder()
    question = "What are the attention weights?"
    retriever(index, factory, embedder=embedder).retrieve(user, question)
    # FakeGenerator rewrites by dropping stop words.
    assert embedder.texts == [question, "attention weights"]


def test_rerank_failure_falls_back_to_fused_order(
    world: World, index: QdrantIndex, factory: sessionmaker[Session]
) -> None:
    user = world.user()
    for body in (
        "attention attention attention heads",
        "attention heads in parallel",
        "attention is computed once",
        "multi head attention",
    ):
        world.document_with_chunk(user, body)
    question = "attention heads"

    failed = retriever(
        index, factory, reranker=FailingReranker(), final_candidates=2
    ).retrieve(user, question)
    plain = retriever(
        index, factory, rerank_enabled=False, final_candidates=2
    ).retrieve(user, question)

    assert len(failed.candidates) == 2
    assert chunk_ids(failed) == chunk_ids(plain)
    assert failed.best_score is None
    reranked = retriever(index, factory, final_candidates=2).retrieve(user, question)
    assert reranked.best_score is not None


def test_stale_and_foreign_chunks_are_dropped_after_hydration(
    world: World, index: QdrantIndex, factory: sessionmaker[Session]
) -> None:
    alice, bob = world.user(), world.user()
    paper = world.document(alice)
    old = world.version(paper, hours=0)
    current = world.version(paper, hours=1)
    stale_chunk = world.chunk(old, "attention stale wording")
    current_chunk = world.chunk(current, "attention current wording")
    bobs = world.document(bob)
    # A point for Bob's chunk that claims to be Alice's: it passes the Qdrant
    # filter, so only the re-read under RLS can stop it.
    foreign_chunk = world.chunk(
        world.version(bobs), "attention secret wording", payload_owner=str(alice)
    )
    question = "attention wording stale current secret"

    # Precondition: the index alone would hand all three to Alice.
    hits = index.search(
        dense=FakeEmbedder(DIM).embed_query(question),
        sparse=encode_query(question),
        flt=build_scope_filter(alice),
        dense_limit=10,
        sparse_limit=10,
        limit=10,
    )
    assert {h.id for h in hits} == {stale_chunk, current_chunk, foreign_chunk}

    result = retriever(index, factory).retrieve(alice, question)
    assert chunk_ids(result) == [current_chunk]
    assert "secret" not in result.candidates[0].text


def test_qdrant_down_raises_retrieval_unavailable() -> None:
    dead = QdrantIndex(QdrantClient(url="http://127.0.0.1:1", timeout=2))
    never_used = sessionmaker[Session]()  # the store is not reached
    with pytest.raises(RetrievalUnavailable):
        retriever(dead, never_used, rewrite_enabled=False).retrieve(
            uuid.uuid4(), "attention"
        )


def test_missing_alias_is_an_empty_result(
    client: QdrantClient, factory: sessionmaker[Session]
) -> None:
    nothing_indexed = QdrantIndex(client, alias=f"missing_{uuid.uuid4().hex[:8]}")
    result = retriever(nothing_indexed, factory).retrieve(uuid.uuid4(), "attention")
    assert result == RetrievalResult([], None)


@pytest.mark.parametrize(
    ("scope", "pick_document", "expected"),
    [
        ("all", None, {"mine_1", "mine_2", "public"}),
        ("mine", None, {"mine_1", "mine_2"}),
        ("public", None, {"public"}),
        ("all", "mine_2", {"mine_2"}),
        ("public", "mine_2", set()),
        ("all", "public", {"public"}),
        ("all", "theirs", set()),
    ],
)
def test_scope_and_document_filters(
    world: World,
    index: QdrantIndex,
    factory: sessionmaker[Session],
    scope: Scope,
    pick_document: str | None,
    expected: set[str],
) -> None:
    alice, bob = world.user(), world.user()
    documents = {
        "mine_1": world.document_with_chunk(alice, "attention in my first paper")[0],
        "mine_2": world.document_with_chunk(alice, "attention in my second paper")[0],
        "public": world.document_with_chunk(None, "attention in a public paper")[0],
        "theirs": world.document_with_chunk(bob, "attention in their paper")[0],
    }
    names = {document: name for name, document in documents.items()}
    document_id = documents[pick_document] if pick_document else None

    result = retriever(index, factory).retrieve(
        alice, "attention paper", scope=scope, document_id=document_id
    )

    assert {names[c.document_id] for c in result.candidates} == expected
