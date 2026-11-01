"""Reading documents: pages of results and the two ways a read can fail."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from explorai_backend.repositories.documents import (
    DocumentRow,
    DocumentsRepository,
    Scope,
    SectionRow,
)


class DocumentNotFound(Exception):
    """Missing, or owned by someone else: the two are deliberately the same."""


class SectionsNotReady(Exception):
    """The document exists but has no successfully extracted version yet."""


@dataclass(frozen=True, slots=True)
class DocumentsPage:
    items: list[DocumentRow]
    next_after: tuple[datetime, uuid.UUID] | None


@dataclass(frozen=True, slots=True)
class SectionsPage:
    items: list[SectionRow]
    next_after: int | None


class DocumentsService:
    def __init__(self, repository: DocumentsRepository) -> None:
        self._repository = repository

    def list(
        self,
        user_id: uuid.UUID,
        scope: Scope,
        limit: int,
        after: tuple[datetime, uuid.UUID] | None,
    ) -> DocumentsPage:
        # One extra row tells whether another page exists without a count query.
        rows = self._repository.list(user_id, scope, limit + 1, after)
        items = rows[:limit]
        if len(rows) <= limit:
            return DocumentsPage(items, None)
        return DocumentsPage(items, (items[-1].created_at, items[-1].id))

    def get(self, user_id: uuid.UUID, document_id: uuid.UUID) -> DocumentRow:
        document = self._repository.get(user_id, document_id)
        if document is None:
            raise DocumentNotFound
        return document

    def sections(
        self,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
        limit: int,
        after_ordinal: int | None,
    ) -> SectionsPage:
        self.get(user_id, document_id)
        rows = self._repository.sections(user_id, document_id, limit + 1, after_ordinal)
        if rows is None:
            raise SectionsNotReady
        items = rows[:limit]
        more = len(rows) > limit
        return SectionsPage(items, items[-1].ordinal if more else None)
