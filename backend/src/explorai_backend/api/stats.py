"""Aggregate statistics for the authenticated user's workspace."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from explorai_backend.api.auth import CurrentUser
from explorai_backend.deps import get_scoped_sessions
from explorai_backend.repositories.conversations import StatsRepository
from explorai_db.rls import ScopedSessions

router = APIRouter(prefix="/api/v1", tags=["stats"])


class StatsOut(BaseModel):
    document_count: int
    indexed_count: int
    conversation_count: int
    answered_count: int
    refusal_count: int
    total_input_tokens: int
    total_output_tokens: int


def get_stats_repo(
    sessions: Annotated[ScopedSessions, Depends(get_scoped_sessions)],
) -> StatsRepository:
    return StatsRepository(sessions)


StatsRepo = Annotated[StatsRepository, Depends(get_stats_repo)]


@router.get("/stats")
def get_stats(user: CurrentUser, repo: StatsRepo) -> StatsOut:
    """Return aggregate counts and token usage for the user's workspace."""
    row = repo.get_stats(user.id)
    return StatsOut(
        document_count=row.document_count,
        indexed_count=row.indexed_count,
        conversation_count=row.conversation_count,
        answered_count=row.answered_count,
        refusal_count=row.refusal_count,
        total_input_tokens=row.total_input_tokens,
        total_output_tokens=row.total_output_tokens,
    )
