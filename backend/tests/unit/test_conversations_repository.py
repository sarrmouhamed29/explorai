"""Unit tests for ConversationsRepository using fake sessions (no database)."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from explorai_backend.repositories.conversations import ConversationsRepository

OWNER_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Fake infrastructure
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    rows: list[Any]
    last_added: Any = field(default=None, init=False)

    def add(self, obj: Any) -> None:
        self.last_added = obj

    def scalars(self, _query: Any) -> Iterator[Any]:
        return iter(self.rows)


class _FakeScopedSessions:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows: list[Any] = rows if rows is not None else []
        self._last_added: Any = None

    @contextmanager
    def scoped(self, _user_id: Any) -> Iterator[_FakeSession]:
        session = _FakeSession(self._rows)
        yield session
        if session.last_added is not None:
            self._last_added = session.last_added

    @property
    def last_inserted(self) -> Any:
        return self._last_added


def _make_conversation(
    owner_id: uuid.UUID,
    question: str = "q",
    created_at: datetime | None = None,
) -> Any:
    from explorai_db.models import Conversation

    conv = Conversation()
    conv.id = uuid.uuid4()
    conv.owner_id = owner_id
    conv.question = question
    conv.answer = "a"
    conv.status = "answered"
    conv.citations = []
    conv.input_tokens = 10
    conv.output_tokens = 5
    conv.created_at = created_at or datetime.now(UTC)
    return conv


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_sessions() -> _FakeScopedSessions:
    return _FakeScopedSessions()


@pytest.fixture
def fake_sessions_empty() -> _FakeScopedSessions:
    return _FakeScopedSessions([])


@pytest.fixture
def fake_sessions_full() -> _FakeScopedSessions:
    rows = [
        _make_conversation(OWNER_ID, f"q{i}", datetime(2026, 1, i + 1, tzinfo=UTC))
        for i in range(3)
    ]
    rows.sort(key=lambda r: r.created_at, reverse=True)
    return _FakeScopedSessions(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_inserts_row(fake_sessions: _FakeScopedSessions) -> None:
    repo = ConversationsRepository(fake_sessions)
    repo.save(OWNER_ID, "q", "a", "answered", "[]", 10, 5)
    row = fake_sessions.last_inserted
    assert row.question == "q"
    assert row.input_tokens == 10


def test_list_returns_empty_when_no_rows(
    fake_sessions_empty: _FakeScopedSessions,
) -> None:
    repo = ConversationsRepository(fake_sessions_empty)
    rows, cursor = repo.list_for_user(OWNER_ID, 20, None)
    assert rows == []
    assert cursor is None


def test_list_returns_next_cursor_when_more(
    fake_sessions_full: _FakeScopedSessions,
) -> None:
    repo = ConversationsRepository(fake_sessions_full)
    rows, cursor = repo.list_for_user(OWNER_ID, 2, None)
    assert len(rows) == 2
    assert cursor is not None
