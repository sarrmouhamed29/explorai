"""SQLAlchemy models of the ingestion schema.

``owner_id`` is a nullable foreign key to ``users``: ``NULL`` is the shared
public corpus, a value is a private document. Uniqueness that involves it uses
``NULLS NOT DISTINCT`` so that public rows (owner ``NULL``) are still unique.

Row-Level Security policies, the ``explorai_app`` role and the ``app_user()``
function live in migrations 0003 and 0005 only: they are server-side objects
that the models cannot express (see ``explorai_db.rls``).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Width of ``chunk_index_entries.index_name`` (migration 0005 states it literally).
INDEX_NAME_MAX_LENGTH = 128


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# Unique on the lowercase form: "Alice@x.org" and "alice@x.org" are one account.
Index("ix_users_email_lower", func.lower(User.email), unique=True)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("kind", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class RawArtifact(Base):
    __tablename__ = "raw_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "external_id",
            "owner_id",
            "sha256",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "external_id",
            "owner_id",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the source says about the document (authors, publication date, abstract).
    source_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )


# Serves the keyset pagination of the documents list (newest first).
Index("ix_documents_created_at_id", Document.created_at.desc(), Document.id.desc())


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "extractor_version"),
        CheckConstraint(
            "status IN ('ok', 'failed', 'unextractable')", name="status_allowed"
        ),
        # Serves "the latest ok version of a document" (explorai_ai.store), run
        # on every question: PostgreSQL does not index foreign key columns.
        Index(
            "ix_document_versions_document_status_created",
            "document_id",
            "status",
            "created_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_artifacts.id"))
    extractor_version: Mapped[str] = mapped_column(String(32))
    text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    minhash_signature: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Set by the indexing pipeline once the version has been cut into chunks, even
    # when that produced none: without it, a version with zero chunks would look
    # "not chunked yet" forever and be chunked again on every pass.
    chunked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Section(Base):
    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("version_id", "ordinal"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text)


class Chunk(Base):
    """A retrieval unit cut from the sections of one version.

    Derived data: it can always be rebuilt from ``sections``. The vectors live in
    Qdrant under the chunk id; the text is only here, so every answer re-reads it
    under Row-Level Security (migration 0005).
    """

    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("version_id", "ordinal"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer)
    text_hash: Mapped[str] = mapped_column(String(64))


class ChunkIndexEntry(Base):
    """A chunk is in the vector index ``index_name``.

    ``index_name`` is the Qdrant collection name, derived from the embedding model
    *and* dimension (``QdrantIndex.collection_name``): changing either one is a new
    index in which every chunk is pending. A chunk without an entry for the
    current index is pending. The old entries stay in place, so the pipeline can
    fill the new collection in the background while an API still configured with
    the old model keeps serving the old one, until its own settings change.
    """

    __tablename__ = "chunk_index_entries"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    # The longest name settings may produce is checked by RagSettings.
    index_name: Mapped[str] = mapped_column(
        String(INDEX_NAME_MAX_LENGTH), primary_key=True
    )
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="running")
    counters: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)
    errors: Mapped[list[str]] = mapped_column(JSONB, default=list)
