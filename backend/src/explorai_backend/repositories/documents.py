"""Reads of documents and their sections.

Each query filters by owner explicitly *and* runs in ``scoped_session``: the
``WHERE`` clause makes the intent readable and testable, the RLS policy is the
safety net if a filter is ever forgotten.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import ColumnElement, or_, select, tuple_

from explorai_db.models import Document, DocumentVersion, Section, Source
from explorai_db.rls import SessionFactory, scoped_session

Scope = Literal["mine", "public", "all"]
Status = Literal["processing", "ready", "failed"]

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
    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory

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
                tuple_(Document.created_at, Document.id) < tuple_(*after)
            )
        with scoped_session(self._factory, user_id) as session:
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
        with scoped_session(self._factory, user_id) as session:
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
        with scoped_session(self._factory, user_id) as session:
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
