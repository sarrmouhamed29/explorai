"""Documents API: listing, one document, its sections, upload and deletion.

Routes are sync ``def`` (run in FastAPI's threadpool) except the upload, which
must be ``async`` to read the request body as a stream; its blocking calls
(Redis, blob store, database) go through ``run_in_threadpool`` instead.
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import redis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from explorai_backend.api.auth import CurrentUser
from explorai_backend.api.pagination import (
    decode_cursor,
    decode_ordinal_cursor,
    encode_cursor,
    encode_ordinal_cursor,
)
from explorai_backend.auth.limits import FixedWindowLimiter
from explorai_backend.config import Settings, get_settings
from explorai_backend.deps import get_blob_store, get_redis, get_session_factory
from explorai_backend.repositories.documents import DocumentRow, DocumentsRepository
from explorai_backend.services.documents import (
    DocumentNotFound,
    DocumentReadOnly,
    DocumentsService,
    QuotaReached,
    SectionsNotReady,
    UploadsService,
)
from explorai_backend.uploads import NotPdf, TooLarge, read_capped
from explorai_db.blobs import BlobStore
from explorai_db.rls import SessionFactory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

UPLOAD_WINDOW_SECONDS = 3600


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


def get_uploads_service(
    settings: Annotated[Settings, Depends(get_settings)],
    factory: Annotated[SessionFactory, Depends(get_session_factory)],
    blobs: Annotated[BlobStore, Depends(get_blob_store)],
) -> UploadsService:
    return UploadsService(
        DocumentsRepository(factory), blobs, settings.max_documents_per_user
    )


def get_upload_limiter(
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[redis.Redis, Depends(get_redis)],
) -> FixedWindowLimiter:
    return FixedWindowLimiter(
        client, "upload:user", settings.upload_max_per_hour, UPLOAD_WINDOW_SECONDS
    )


Service = Annotated[DocumentsService, Depends(get_documents_service)]
Uploads = Annotated[UploadsService, Depends(get_uploads_service)]
UploadLimiter = Annotated[FixedWindowLimiter, Depends(get_upload_limiter)]
AppSettings = Annotated[Settings, Depends(get_settings)]
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


# The body is raw bytes, not multipart (Starlette buffers a whole multipart body
# before the handler runs, so no streamed cap would be possible). FastAPI cannot
# infer a body the handler reads itself, so the schema is declared by hand for
# the generated client.
_PDF_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/pdf": {"schema": {"type": "string", "format": "binary"}}
        },
    }
}


def _too_large() -> HTTPException:
    return HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "upload too large")


async def _release(limiter: FixedWindowLimiter, key: str) -> None:
    try:
        await run_in_threadpool(limiter.release, key)
    except redis.RedisError:
        # Errs on the strict side: one extra count until the window ends.
        logger.warning("could not release an upload reservation")


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=_PDF_BODY,
    responses={
        200: {"model": DocumentOut, "description": "Already uploaded by this user"},
        401: {"description": "Not authenticated"},
        403: {"description": "Origin not allowed"},
        409: {"description": "Document quota reached"},
        413: {"description": "Upload too large"},
        415: {"description": "Not a PDF"},
        429: {"description": "Too many uploads"},
        503: {"description": "Session store, rate limiter or database unavailable"},
    },
)
async def upload_document(
    request: Request,
    response: Response,
    user: CurrentUser,
    uploads: Uploads,
    limiter: UploadLimiter,
    settings: AppSettings,
    filename: Annotated[str | None, Query(max_length=1024)] = None,
) -> DocumentOut:
    """Upload a private PDF as the raw request body (``Content-Type`` is ignored).

    ``filename`` only gives the title. The answer is ``202`` with the new
    document, still ``processing``, or ``200`` with the existing one when this
    user already uploaded the same bytes.
    """
    key = str(user.id)
    try:
        # Reserve first (atomic INCR), so a burst of parallel uploads cannot all
        # pass a read-then-count check.
        reserved = await run_in_threadpool(limiter.hit, key)
    except redis.RedisError:
        logger.warning("rate limiter unavailable")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "rate limiter unavailable"
        ) from None
    # Only uploads that create a document consume the hourly budget: every other
    # outcome (refusal, re-sent file, error) gives its reservation back.
    created = False
    try:
        if reserved > limiter.limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many uploads")
        declared = request.headers.get("content-length", "")
        # A declared length over the cap is refused without reading; the
        # streamed cap below holds whatever the header says, or without one.
        if declared.isdigit() and int(declared) > settings.upload_max_bytes:
            raise _too_large()
        try:
            data = await read_capped(request.stream(), settings.upload_max_bytes)
        except TooLarge:
            raise _too_large() from None
        try:
            result = await run_in_threadpool(uploads.upload, user.id, data, filename)
        except NotPdf:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "only PDF files are accepted"
            ) from None
        except QuotaReached:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "document quota reached"
            ) from None
        created = result.created
    finally:
        if not created:
            await _release(limiter, key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return _out(result.document)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **_NOT_FOUND,
        403: {"description": "Public document, or origin not allowed"},
    },
)
def delete_document(
    document_id: uuid.UUID, user: CurrentUser, service: Service
) -> None:
    """Delete one of your documents; its stored file is collected later."""
    try:
        service.delete(user.id, document_id)
    except DocumentNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found") from None
    except DocumentReadOnly:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "public documents cannot be deleted"
        ) from None
