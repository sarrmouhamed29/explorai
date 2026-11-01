"""Read routes of the documents API (listing, one document, its sections)."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from explorai_backend.api.auth import CurrentUser
from explorai_backend.api.pagination import (
    decode_cursor,
    decode_ordinal_cursor,
    encode_cursor,
    encode_ordinal_cursor,
)
from explorai_backend.deps import get_session_factory
from explorai_backend.repositories.documents import DocumentRow, DocumentsRepository
from explorai_backend.services.documents import (
    DocumentNotFound,
    DocumentsService,
    SectionsNotReady,
)
from explorai_db.rls import SessionFactory

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str | None
    source_kind: str
    owned: bool
    status: Literal["processing", "ready", "failed"]
    created_at: datetime


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    next_cursor: str | None


class SectionOut(BaseModel):
    ordinal: int
    heading: str | None
    text: str


class SectionPage(BaseModel):
    items: list[SectionOut]
    next_cursor: str | None


def get_documents_service(
    factory: Annotated[SessionFactory, Depends(get_session_factory)],
) -> DocumentsService:
    return DocumentsService(DocumentsRepository(factory))


Service = Annotated[DocumentsService, Depends(get_documents_service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query()]

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Not found, or owned by someone else"}
}


def _out(row: DocumentRow) -> DocumentOut:
    return DocumentOut(
        id=row.id,
        title=row.title,
        source_kind=row.source_kind,
        owned=row.owned,
        status=row.status,
        created_at=row.created_at,
    )


def _invalid_cursor() -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid cursor")


@router.get("", responses={422: {"description": "Invalid parameter or cursor"}})
def list_documents(
    user: CurrentUser,
    service: Service,
    scope: Literal["mine", "public", "all"] = "all",
    limit: Limit = 20,
    cursor: Cursor = None,
) -> DocumentPage:
    try:
        after = None if cursor is None else decode_cursor(cursor)
    except ValueError:
        raise _invalid_cursor() from None
    page = service.list(user.id, scope, limit, after)
    return DocumentPage(
        items=[_out(row) for row in page.items],
        next_cursor=(
            None if page.next_after is None else encode_cursor(*page.next_after)
        ),
    )


@router.get("/{document_id}", responses=_NOT_FOUND)
def get_document(
    document_id: uuid.UUID, user: CurrentUser, service: Service
) -> DocumentOut:
    try:
        return _out(service.get(user.id, document_id))
    except DocumentNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found") from None


@router.get(
    "/{document_id}/sections",
    responses={**_NOT_FOUND, 409: {"description": "Sections not ready yet"}},
)
def get_sections(
    document_id: uuid.UUID,
    user: CurrentUser,
    service: Service,
    limit: Limit = 20,
    cursor: Cursor = None,
) -> SectionPage:
    try:
        after = None if cursor is None else decode_ordinal_cursor(cursor)
    except ValueError:
        raise _invalid_cursor() from None
    try:
        page = service.sections(user.id, document_id, limit, after)
    except DocumentNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found") from None
    except SectionsNotReady:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "sections are not ready yet"
        ) from None
    return SectionPage(
        items=[
            SectionOut(ordinal=s.ordinal, heading=s.heading, text=s.text)
            for s in page.items
        ],
        next_cursor=(
            None if page.next_after is None else encode_ordinal_cursor(page.next_after)
        ),
    )
