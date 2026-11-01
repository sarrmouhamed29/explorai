"""Documents: reading, uploading and deleting, and the ways each can fail."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from explorai_backend.repositories.documents import (
    DocumentRow,
    DocumentsRepository,
    Scope,
    SectionRow,
)
from explorai_backend.uploads import clean_title, require_pdf
from explorai_db.blobs import BlobStore


class DocumentNotFound(Exception):
    """Missing, or owned by someone else: the two are deliberately the same."""


class SectionsNotReady(Exception):
    """The document exists but has no successfully extracted version yet."""


class DocumentReadOnly(Exception):
    """A public document: part of the shared corpus, nobody's to delete."""


class QuotaReached(Exception):
    """The user already owns the maximum number of documents."""


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

    def delete(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        outcome = self._repository.delete(user_id, document_id)
        if outcome == "not_found":
            raise DocumentNotFound
        if outcome == "read_only":
            raise DocumentReadOnly


@dataclass(frozen=True, slots=True)
class Upload:
    document: DocumentRow
    created: bool


class UploadsService:
    """Turns an already size-capped body into a private document.

    Order matters: the type check and the idempotency lookup come before the
    quota, so re-sending a file already stored is answered even at the quota;
    the blob is written before the rows, so a committed row never points at
    missing bytes (an orphan blob after a crash is collected by the pipeline).
    """

    def __init__(
        self, repository: DocumentsRepository, blobs: BlobStore, max_documents: int
    ) -> None:
        self._repository = repository
        self._blobs = blobs
        self._max_documents = max_documents

    def upload(self, user_id: uuid.UUID, data: bytes, filename: str | None) -> Upload:
        require_pdf(data)
        sha256 = hashlib.sha256(data).hexdigest()
        existing = self._repository.find_upload(user_id, sha256)
        if existing is not None:
            return Upload(existing, created=False)
        # Checked outside the insert transaction: two concurrent uploads at the
        # quota can both pass. Acceptable for a soft limit, which the hourly
        # limiter already bounds to a few documents.
        if self._repository.count_owned(user_id) >= self._max_documents:
            raise QuotaReached
        key = self._blobs.put(data)
        document, created = self._repository.create_upload(
            user_id, sha256, key, clean_title(filename)
        )
        return Upload(document, created)
