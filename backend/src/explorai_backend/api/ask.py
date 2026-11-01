"""Question answering over the user's and the public documents.

The route is a sync ``def``: FastAPI runs it in its threadpool, where the blocking
Redis, PostgreSQL, Qdrant and provider calls of the pipeline do not stall the
event loop. Its job is mapping: domain outcomes to HTTP codes.
"""

import uuid
from typing import Annotated, Any, Literal

import redis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from explorai_ai.config import RagSettings
from explorai_ai.pipeline import AnswerPipeline
from explorai_ai.scope import Scope
from explorai_backend.api.auth import CurrentUser
from explorai_backend.auth.limits import FixedWindowLimiter
from explorai_backend.deps import (
    get_answer_pipeline,
    get_rag_settings,
    get_redis,
    get_scoped_sessions,
)
from explorai_backend.repositories.documents import DocumentsRepository
from explorai_backend.services.ask import (
    AnswerUnavailable,
    AskService,
    RateLimiterUnavailable,
    TooManyQuestions,
)
from explorai_backend.services.documents import DocumentNotFound
from explorai_db.rls import ScopedSessions

router = APIRouter(prefix="/api/v1", tags=["ask"])

ASK_WINDOW_SECONDS = 3600
QUESTION_MAX_CHARS = 2000


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=QUESTION_MAX_CHARS)
    scope: Scope = "all"
    document_id: uuid.UUID | None = None

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # Whitespace passes min_length but gives the pipeline nothing to search.
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class CitationOut(BaseModel):
    source: int
    quote: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str | None
    section: str | None


class UsageOut(BaseModel):
    input_tokens: int
    output_tokens: int


class AskResponse(BaseModel):
    status: Literal["answered", "insufficient_evidence"]
    answer: str | None
    citations: list[CitationOut]
    usage: UsageOut


def get_ask_limiter(
    settings: Annotated[RagSettings, Depends(get_rag_settings)],
    client: Annotated[redis.Redis, Depends(get_redis)],
) -> FixedWindowLimiter:
    return FixedWindowLimiter(
        client, "ask:user", settings.ask_max_per_hour, ASK_WINDOW_SECONDS
    )


def get_ask_service(
    pipeline: Annotated[AnswerPipeline, Depends(get_answer_pipeline)],
    sessions: Annotated[ScopedSessions, Depends(get_scoped_sessions)],
    limiter: Annotated[FixedWindowLimiter, Depends(get_ask_limiter)],
) -> AskService:
    return AskService(pipeline, DocumentsRepository(sessions), limiter)


Service = Annotated[AskService, Depends(get_ask_service)]

_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not authenticated"},
    403: {"description": "Origin not allowed"},
    404: {"description": "document_id not found, or owned by someone else"},
    422: {"description": "Empty, blank or too long question, or invalid scope"},
    429: {"description": "Too many questions this hour"},
    503: {
        "description": "Rate limiter, vector store, model provider or database "
        "unavailable"
    },
}


@router.post("/ask", responses=_RESPONSES)
def ask(request: AskRequest, user: CurrentUser, service: Service) -> AskResponse:
    """Answer from the documents the user can see, citing verified quotes.

    ``status`` is ``answered`` (at least one citation, each quote checked against
    its chunk) or ``insufficient_evidence`` (``answer`` is null): a refusal is a
    normal ``200`` answer, not an error.
    """
    try:
        result = service.ask(
            user.id, request.question, request.scope, request.document_id
        )
    except RateLimiterUnavailable:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "rate limiter unavailable"
        ) from None
    except TooManyQuestions:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many questions"
        ) from None
    except DocumentNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found") from None
    except AnswerUnavailable:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "answering is unavailable right now"
        ) from None
    return AskResponse(
        status=result.status,
        answer=result.answer,
        citations=[
            CitationOut(
                source=citation.source,
                quote=citation.quote,
                chunk_id=citation.chunk_id,
                document_id=citation.document_id,
                document_title=citation.document_title,
                section=citation.heading,
            )
            for citation in result.citations
        ],
        usage=UsageOut(
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        ),
    )
