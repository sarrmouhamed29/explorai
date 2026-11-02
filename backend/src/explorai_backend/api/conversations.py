"""Conversation history for the authenticated user.

``GET /api/v1/conversations`` returns the most recent exchanges, newest first,
with keyset pagination identical to the documents list.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from explorai_backend.api.auth import CurrentUser
from explorai_backend.deps import get_scoped_sessions
from explorai_backend.repositories.conversations import ConversationsRepository
from explorai_db.rls import ScopedSessions

router = APIRouter(prefix="/api/v1", tags=["conversations"])


class CitationOut(BaseModel):
    model_config = {"extra": "allow"}


class ConversationOut(BaseModel):
    id: uuid.UUID
    question: str
    answer: str | None
    status: str
    citations: list[Any]
    created_at: datetime


class ConversationsPage(BaseModel):
    items: list[ConversationOut]
    next_cursor: str | None


def get_conversations_repo(
    sessions: Annotated[ScopedSessions, Depends(get_scoped_sessions)],
) -> ConversationsRepository:
    return ConversationsRepository(sessions)


Repo = Annotated[ConversationsRepository, Depends(get_conversations_repo)]


@router.get("/conversations")
def list_conversations(
    user: CurrentUser,
    repo: Repo,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
) -> ConversationsPage:
    """Return the user's conversation history, newest first."""
    rows, next_cursor = repo.list_for_user(user.id, limit, cursor)
    return ConversationsPage(
        items=[
            ConversationOut(
                id=row.id,
                question=row.question,
                answer=row.answer,
                status=row.status,
                citations=row.citations,
                created_at=row.created_at,
            )
            for row in rows
        ],
        next_cursor=next_cursor,
    )
