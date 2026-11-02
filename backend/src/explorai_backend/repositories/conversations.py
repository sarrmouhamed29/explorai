"""Conversation history and aggregate statistics for one user.

Each query filters by owner explicitly *and* runs inside
``ScopedSessions.scoped``: the explicit WHERE makes intent readable and
testable; the RLS policy is the safety net.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, tuple_

from explorai_backend.api.pagination import decode_cursor, encode_cursor
from explorai_db.models import (
    Chunk,
    ChunkIndexEntry,
    Conversation,
    Document,
    DocumentVersion,
)
from explorai_db.rls import ScopedSessions


@dataclass(frozen=True, slots=True)
class ConversationRow:
    id: uuid.UUID
    owner_id: uuid.UUID
    question: str
    answer: str | None
    status: str
    citations: list[object]
    input_tokens: int
    output_tokens: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StatsRow:
    document_count: int
    indexed_count: int
    conversation_count: int
    answered_count: int
    refusal_count: int
    total_input_tokens: int
    total_output_tokens: int


class ConversationsRepository:
    def __init__(self, sessions: ScopedSessions) -> None:
        self._sessions = sessions

    def save(
        self,
        owner_id: uuid.UUID,
        question: str,
        answer: str | None,
        status: str,
        citations_json: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        conv = Conversation(
            owner_id=owner_id,
            question=question,
            answer=answer,
            status=status,
            citations=json.loads(citations_json),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        with self._sessions.scoped(owner_id) as session:
            session.add(conv)

    def list_for_user(
        self,
        owner_id: uuid.UUID,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[ConversationRow], str | None]:
        query = (
            select(Conversation)
            .where(Conversation.owner_id == owner_id)
            .order_by(Conversation.created_at.desc(), Conversation.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            ts, cid = decode_cursor(cursor)
            query = query.where(
                tuple_(Conversation.created_at, Conversation.id) < (ts, cid)
            )
        with self._sessions.scoped(owner_id) as session:
            objects = list(session.scalars(query))

        has_more = len(objects) > limit
        page = objects[:limit]
        next_cursor = (
            encode_cursor(page[-1].created_at, page[-1].id) if has_more else None
        )

        return (
            [
                ConversationRow(
                    id=obj.id,
                    owner_id=obj.owner_id,
                    question=obj.question,
                    answer=obj.answer,
                    status=obj.status,
                    citations=obj.citations,
                    input_tokens=obj.input_tokens,
                    output_tokens=obj.output_tokens,
                    created_at=obj.created_at,
                )
                for obj in page
            ],
            next_cursor,
        )


class StatsRepository:
    def __init__(self, sessions: ScopedSessions) -> None:
        self._sessions = sessions

    def get_stats(self, owner_id: uuid.UUID) -> StatsRow:
        doc_count_query = (
            select(func.count())
            .select_from(Document)
            .where(Document.owner_id == owner_id)
        )
        # A document is "indexed" when it has at least one chunk with an index entry.
        indexed_subq = (
            select(Document.id)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .join(Chunk, Chunk.version_id == DocumentVersion.id)
            .join(ChunkIndexEntry, ChunkIndexEntry.chunk_id == Chunk.id)
            .where(Document.owner_id == owner_id)
            .distinct()
            .subquery()
        )
        indexed_count_query = select(func.count()).select_from(indexed_subq)

        conv_query = select(
            func.count().label("conversation_count"),
            func.coalesce(
                func.sum(
                    func.cast(Conversation.status == "answered", type_=func.int4().type)
                ),
                0,
            ).label("answered_count"),
            func.coalesce(func.sum(Conversation.input_tokens), 0).label(
                "total_input_tokens"
            ),
            func.coalesce(func.sum(Conversation.output_tokens), 0).label(
                "total_output_tokens"
            ),
        ).where(Conversation.owner_id == owner_id)

        with self._sessions.scoped(owner_id) as session:
            document_count = session.scalar(doc_count_query) or 0
            indexed_count = session.scalar(indexed_count_query) or 0
            conv_row = session.execute(conv_query).one()

        conversation_count = conv_row.conversation_count or 0
        answered_count = int(conv_row.answered_count or 0)

        return StatsRow(
            document_count=document_count,
            indexed_count=indexed_count,
            conversation_count=conversation_count,
            answered_count=answered_count,
            refusal_count=conversation_count - answered_count,
            total_input_tokens=int(conv_row.total_input_tokens or 0),
            total_output_tokens=int(conv_row.total_output_tokens or 0),
        )
