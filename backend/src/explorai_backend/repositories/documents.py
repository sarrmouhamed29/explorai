"""Documents and their sections: reads, private uploads, deletion.

Each query filters by owner explicitly *and* runs in ``ScopedSessions.scoped``: the
``WHERE`` clause makes the intent readable and testable, the RLS policy is the
safety net if a filter is ever forgotten.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import ColumnElement, delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from explorai_db.models import Document, DocumentVersion, RawArtifact, Section, Source
from explorai_db.rls import ScopedSessions

Scope = Literal["mine", "public", "all"]
Status = Literal["processing", "ready", "failed"]
DeleteOutcome = Literal["deleted", "not_found", "read_only"]

# The source row every upload belongs to, created by migration 0003.
UPLOAD_SOURCE = ("upload", "upload")

_STATUS_BY_VERSION: dict[str, Status] = {
    "ok": "ready",
    "failed": "failed",
    "unextractable": "failed",
}


@dataclass(frozen=True, slots=True)
class DocumentRow:
    id: uuid.UUID
    title: str | None
    source_kind: str
    owned: bool
    status: Status
    created_at: datetime
    # Whether the document is searchable. The queries here leave it False; the
    # service fills it with one ChunkStore.indexed_state query per page.
    indexed: bool = False


@dataclass(frozen=True, slots=True)
class SectionRow:
    ordinal: int
    heading: str | None
    text: str


def _visible(user_id: uuid.UUID, scope: Scope) -> ColumnElement[bool]:
    if scope == "mine":
        return Document.owner_id == user_id
    if scope == "public":
        return Document.owner_id.is_(None)
    return or_(Document.owner_id.is_(None), Document.owner_id == user_id)


# Status comes from the most recent version, whatever the extractor: no version
# yet means the pipeline has not processed the document.
_LATEST_VERSION_STATUS = (
    select(DocumentVersion.status)
    .where(DocumentVersion.document_id == Document.id)
    .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
    .limit(1)
    .scalar_subquery()
)


def _row(
    user_id: uuid.UUID, document: Document, kind: str, version_status: str | None
) -> DocumentRow:
    status: Status = (
        "processing" if version_status is None else _STATUS_BY_VERSION[version_status]
    )
    return DocumentRow(
        document.id,
        document.title,
        kind,
        document.owner_id == user_id,
        status,
        document.created_at,
    )


# Aliases because the method named ``list`` shadows the builtin inside the class.
DocumentRows = list[DocumentRow]
SectionRows = list[SectionRow]


class DocumentsRepository:
    def __init__(self, sessions: ScopedSessions) -> None:
        self._sessions = sessions

    def list(
        self,
        user_id: uuid.UUID,
        scope: Scope,
        limit: int,
        after: tuple[datetime, uuid.UUID] | None,
    ) -> DocumentRows:
        query = (
            select(Document, Source.kind, _LATEST_VERSION_STATUS)
            .join(Source, Source.id == Document.source_id)
            .where(_visible(user_id, scope))
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(limit)
        )
        if after is not None:
            query = query.where(
                tuple_(Document.created_at, Document.id) < (after[0], after[1])
            )
        with self._sessions.scoped(user_id) as session:
            return [
                _row(user_id, document, kind, status)
                for document, kind, status in session.execute(query)
            ]

    def get(self, user_id: uuid.UUID, document_id: uuid.UUID) -> DocumentRow | None:
        query = (
            select(Document, Source.kind, _LATEST_VERSION_STATUS)
            .join(Source, Source.id == Document.source_id)
            .where(Document.id == document_id, _visible(user_id, "all"))
        )
        with self._sessions.scoped(user_id) as session:
            found = session.execute(query).one_or_none()
            if found is None:
                return None
            document, kind, status = found
            return _row(user_id, document, kind, status)

    def sections(
        self,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
        limit: int,
        after_ordinal: int | None,
    ) -> SectionRows | None:
        """Sections of the latest ``ok`` version; ``None`` without such a version."""
        version_query = (
            select(DocumentVersion.id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.id == document_id,
                _visible(user_id, "all"),
                DocumentVersion.status == "ok",
            )
            .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
            .limit(1)
        )
        with self._sessions.scoped(user_id) as session:
            version_id = session.scalars(version_query).first()
            if version_id is None:
                return None
            query = (
                select(Section.ordinal, Section.heading, Section.text)
                .where(Section.version_id == version_id)
                .order_by(Section.ordinal)
                .limit(limit)
            )
            if after_ordinal is not None:
                query = query.where(Section.ordinal > after_ordinal)
            return [SectionRow(*row) for row in session.execute(query)]

    def _find_upload(
        self, session: Session, user_id: uuid.UUID, sha256: str
    ) -> DocumentRow | None:
        query = (
            select(Document, Source.kind, _LATEST_VERSION_STATUS)
            .join(Source, Source.id == Document.source_id)
            .where(
                Source.kind == UPLOAD_SOURCE[0],
                Source.name == UPLOAD_SOURCE[1],
                Document.external_id == sha256,
                Document.owner_id == user_id,
            )
        )
        found = session.execute(query).one_or_none()
        if found is None:
            return None
        document, kind, status = found
        return _row(user_id, document, kind, status)

    def find_upload(self, user_id: uuid.UUID, sha256: str) -> DocumentRow | None:
        """The document this user already created by uploading these bytes."""
        with self._sessions.scoped(user_id) as session:
            return self._find_upload(session, user_id, sha256)

    def count_owned(self, user_id: uuid.UUID) -> int:
        """Private documents of this user, whatever their source (the quota)."""
        query = (
            select(func.count())
            .select_from(Document)
            .where(Document.owner_id == user_id)
        )
        with self._sessions.scoped(user_id) as session:
            return session.scalar(query) or 0

    def create_upload(
        self, user_id: uuid.UUID, sha256: str, storage_key: str, title: str
    ) -> tuple[DocumentRow, bool]:
        """Record an uploaded file: its artifact and its document, in one transaction.

        Returns the document and whether it was created. The document identity is
        (upload source, SHA-256, owner), so a second upload of the same bytes by
        the same owner returns the first document. ``ON CONFLICT DO NOTHING``
        makes that hold for two concurrent uploads too: the unique constraint
        decides, and the loser reads the winner's row instead of failing.
        """
        with self._sessions.scoped(user_id) as session:
            source_id = session.scalars(
                select(Source.id).where(
                    Source.kind == UPLOAD_SOURCE[0],
                    Source.name == UPLOAD_SOURCE[1],
                )
            ).one()
            identity = {
                "source_id": source_id,
                "external_id": sha256,
                "owner_id": user_id,
            }
            session.execute(
                insert(RawArtifact)
                .values(
                    # Not fetched from anywhere: an opaque reference to the bytes.
                    url=f"upload:{sha256}",
                    sha256=sha256,
                    storage_key=storage_key,
                    **identity,
                )
                .on_conflict_do_nothing()
            )
            created = session.scalar(
                insert(Document)
                # id and created_at come from the column defaults.
                .values(title=title, **identity)
                .on_conflict_do_nothing()
                .returning(Document.id)
            )
            row = self._find_upload(session, user_id, sha256)
            assert row is not None  # inserted or already there, in this transaction
            return row, created is not None

    def delete(self, user_id: uuid.UUID, document_id: uuid.UUID) -> DeleteOutcome:
        """Delete one of the user's documents with its versions, sections, artifacts.

        Versions and sections go through ``ON DELETE CASCADE``; versions of the
        user's other documents that pointed at a deleted one as canonical keep
        their row with ``duplicate_of`` set to ``NULL``. The artifacts of the
        document's identity are deleted after it, since its versions referenced
        them. Blobs are left alone: under RLS this transaction cannot see whether
        another owner's artifact stores the same bytes, so the pipeline
        garbage-collects unreferenced blobs.
        """
        with self._sessions.scoped(user_id) as session:
            document = session.scalars(
                select(Document).where(
                    Document.id == document_id, _visible(user_id, "all")
                )
            ).one_or_none()
            if document is None:
                return "not_found"
            if document.owner_id is None:
                return "read_only"
            source_id, external_id = document.source_id, document.external_id
            session.execute(
                delete(Document).where(
                    Document.id == document_id, Document.owner_id == user_id
                )
            )
            session.execute(
                delete(RawArtifact).where(
                    RawArtifact.source_id == source_id,
                    RawArtifact.external_id == external_id,
                    RawArtifact.owner_id == user_id,
                )
            )
            return "deleted"
