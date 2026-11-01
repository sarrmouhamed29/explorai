"""Helpers shared by the document integration tests.

Rows are inserted as the table owner (no RLS), standing in for the pipeline.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from fastapi.testclient import TestClient
from qdrant_client.models import Filter
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from explorai_ai.config import RagSettings
from explorai_ai.fakes import FakeEmbedder, FakeGenerator, FakeReranker
from explorai_ai.index import QdrantIndex
from explorai_ai.models import GeneratedAnswer, PromptSource
from explorai_ai.pipeline import AnswerPipeline
from explorai_ai.retriever import Retriever
from explorai_ai.sparse import encode_query
from explorai_ai.store import ChunkStore
from explorai_db.models import (
    Chunk,
    Document,
    DocumentVersion,
    RawArtifact,
    Section,
    Source,
    User,
)
from explorai_db.rls import ScopedSessions
from explorai_pipelines.indexing import index_pending
from explorai_pipelines.repository import Repository

PASSWORD = "correct horse battery staple"


def login_as(client: TestClient, email: str) -> uuid.UUID:
    """Create the account (first time) and log in; return its user id."""
    response = client.post(
        "/api/v1/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    user_id = uuid.UUID(response.json()["id"])
    login_as_existing(client, email)
    return user_id


def login_as_existing(client: TestClient, email: str) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 204, response.text


def add_document(
    factory: sessionmaker[Session],
    owner: uuid.UUID | None,
    *,
    title: str,
    created_at: datetime,
    status: str | None = None,
    sections: int = 0,
    kind: str = "arxiv",
) -> uuid.UUID:
    """Insert as the table owner (no RLS); status None means no version yet."""
    with factory.begin() as session:
        source = session.query(Source).filter_by(kind=kind, name="t").one_or_none()
        if source is None:
            source = Source(kind=kind, name="t", config={})
            session.add(source)
            session.flush()
        external_id = str(uuid.uuid4())
        document = Document(
            source_id=source.id,
            external_id=external_id,
            title=title,
            owner_id=owner,
            created_at=created_at,
        )
        session.add(document)
        session.flush()
        if status is not None:
            artifact = RawArtifact(
                source_id=source.id,
                external_id=external_id,
                url="u",
                sha256=uuid.uuid4().hex * 2,
                storage_key="k",
                owner_id=owner,
            )
            session.add(artifact)
            session.flush()
            version = DocumentVersion(
                document_id=document.id,
                artifact_id=artifact.id,
                extractor_version="v1",
                status=status,
            )
            session.add(version)
            session.flush()
            for ordinal in range(sections):
                session.add(
                    Section(
                        version_id=version.id,
                        ordinal=ordinal,
                        heading=f"h{ordinal}",
                        text=f"text {ordinal}",
                    )
                )
        return document.id


def add_version(
    factory: sessionmaker[Session],
    document_id: uuid.UUID,
    status: str,
    created_at: datetime,
    *,
    texts: tuple[str, ...] = (),
    version_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Add one more version (own artifact) to an existing document."""
    with factory.begin() as session:
        document = session.get_one(Document, document_id)
        artifact = RawArtifact(
            source_id=document.source_id,
            external_id=document.external_id,
            url="u",
            sha256=uuid.uuid4().hex * 2,
            storage_key="k",
            owner_id=document.owner_id,
        )
        session.add(artifact)
        session.flush()
        version = DocumentVersion(
            document_id=document_id,
            artifact_id=artifact.id,
            extractor_version=f"v-{uuid.uuid4().hex[:8]}",
            status=status,
            created_at=created_at,
        )
        if version_id is not None:
            version.id = version_id
        session.add(version)
        session.flush()
        for ordinal, text in enumerate(texts):
            session.add(
                Section(version_id=version.id, ordinal=ordinal, heading=None, text=text)
            )
        return version.id


def index_everything(
    factory: sessionmaker[Session], index: QdrantIndex, settings: RagSettings
) -> dict[str, int]:
    """One pass of the real indexing function (chunk, embed, upsert, record).

    The pipeline's repository runs as the table owner, as the pipelines service
    does; the embedder is the fake of ``explorai_ai``.
    """
    counters: dict[str, int] = index_pending(
        Repository(factory),
        FakeEmbedder(dimension=settings.embedding_dimension),
        index,
        settings,
    )
    assert counters["skipped"] == 0, counters
    assert counters["embedding_failed"] == 0, counters
    return counters


def document_with_text(
    factory: sessionmaker[Session],
    owner: uuid.UUID | None,
    title: str,
    text: str,
    created_at: datetime,
) -> uuid.UUID:
    """A ready document (one ``ok`` version, one section holding ``text``)."""
    document = add_document(factory, owner, title=title, created_at=created_at)
    add_version(factory, document, "ok", created_at, texts=(text,))
    return document


def add_user(factory: sessionmaker[Session], email: str) -> uuid.UUID:
    """An account row inserted as the table owner, for tests that skip HTTP."""
    with factory.begin() as session:
        user = User(email=email, password_hash="not-a-real-hash")
        session.add(user)
        session.flush()
        return user.id


def chunk_ids_of(
    factory: sessionmaker[Session], document_id: uuid.UUID
) -> set[uuid.UUID]:
    """Every chunk id of every version of a document (read as the table owner)."""
    with factory.begin() as session:
        return set(
            session.scalars(
                select(Chunk.id)
                .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
                .where(DocumentVersion.document_id == document_id)
            )
        )


def raw_search(
    index: QdrantIndex, settings: RagSettings, question: str, flt: Filter
) -> set[uuid.UUID]:
    """The point ids Qdrant alone returns for ``question`` under ``flt``.

    No PostgreSQL re-read: this shows what the vector index would hand over, so a
    test can tell which defence stopped a chunk.
    """
    embedder = FakeEmbedder(dimension=settings.embedding_dimension)
    hits = index.search(
        collection=settings.index_name,
        dense=embedder.embed_query(question),
        sparse=encode_query(question),
        flt=flt,
        dense_limit=settings.dense_candidates,
        sparse_limit=settings.sparse_candidates,
        limit=settings.fused_candidates,
    )
    return {hit.id for hit in hits}


@dataclass
class RecordingGenerator:
    """The fake generator, remembering every prompt it was given.

    ``answers`` holds one ``(question, sources)`` pair per ``answer`` call, so a
    test can read exactly what would have been sent to the model.
    """

    inner: FakeGenerator = field(default_factory=FakeGenerator)
    rewrites: list[str] = field(default_factory=list)
    answers: list[tuple[str, list[PromptSource]]] = field(default_factory=list)

    def rewrite_queries(self, question: str, max_queries: int) -> list[str]:
        self.rewrites.append(question)
        return self.inner.rewrite_queries(question, max_queries)

    def answer(self, question: str, sources: Sequence[PromptSource]) -> GeneratedAnswer:
        self.answers.append((question, list(sources)))
        return self.inner.answer(question, sources)


@dataclass
class RagStack:
    retriever: Retriever
    pipeline: AnswerPipeline
    generator: RecordingGenerator


def rag_stack(
    factory: sessionmaker[Session],
    index: QdrantIndex,
    settings: RagSettings,
    *,
    store: ChunkStore | None = None,
) -> RagStack:
    """The production retriever and pipeline over the fakes, as the API wires them.

    The chunk store re-reads texts through ``ScopedSessions`` (the API role under
    RLS), exactly as ``deps.get_chunk_store`` builds it for a request.
    """
    generator = RecordingGenerator()
    retriever = Retriever(
        index=index,
        embedder=FakeEmbedder(dimension=settings.embedding_dimension),
        reranker=FakeReranker(),
        generator=generator,
        store=store or ChunkStore(ScopedSessions(factory)),
        settings=settings,
    )
    return RagStack(
        retriever, AnswerPipeline(retriever, generator, settings), generator
    )
