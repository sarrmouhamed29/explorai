"""Unit tests for GET /api/v1/conversations and GET /api/v1/stats routes."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from explorai_backend import deps
from explorai_backend.api import conversations as conversations_api
from explorai_backend.api import stats as stats_api
from explorai_backend.api.auth import current_user
from explorai_backend.config import get_settings
from explorai_backend.main import create_app
from explorai_backend.repositories.conversations import ConversationRow, StatsRow
from explorai_backend.services.auth import PublicUser

USER = PublicUser(id=uuid.uuid4(), email="test@example.org")
CONVERSATION_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/15")
    monkeypatch.setenv("BLOB_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_PROVIDER", "fake")
    get_settings.cache_clear()
    deps.get_rag_settings.cache_clear()
    yield
    deps.get_rag_settings.cache_clear()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class EmptyConversationsRepo:
    def list_for_user(
        self,
        owner_id: uuid.UUID,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[ConversationRow], str | None]:
        return [], None


class OneConversationRepo:
    def list_for_user(
        self,
        owner_id: uuid.UUID,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[ConversationRow], str | None]:
        row = ConversationRow(
            id=CONVERSATION_ID,
            owner_id=owner_id,
            question="What?",
            answer="Because.",
            status="answered",
            citations=[],
            input_tokens=5,
            output_tokens=3,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        return [row], None


class EmptyStatsRepo:
    def get_stats(self, owner_id: uuid.UUID) -> StatsRow:
        return StatsRow(
            document_count=0,
            indexed_count=0,
            conversation_count=0,
            answered_count=0,
            refusal_count=0,
            total_input_tokens=0,
            total_output_tokens=0,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_auth() -> FastAPI:
    app = create_app()
    app.dependency_overrides[current_user] = lambda: USER
    return app


@pytest.fixture
def client_auth(app_auth: FastAPI) -> TestClient:
    return TestClient(app_auth)


@pytest.fixture
def client_no_auth() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def empty_conversations_repo(app_auth: FastAPI) -> None:
    app_auth.dependency_overrides[conversations_api.get_conversations_repo] = lambda: (
        EmptyConversationsRepo()
    )


@pytest.fixture
def one_conversation_repo(app_auth: FastAPI) -> None:
    app_auth.dependency_overrides[conversations_api.get_conversations_repo] = lambda: (
        OneConversationRepo()
    )


@pytest.fixture
def empty_stats_repo(app_auth: FastAPI) -> None:
    app_auth.dependency_overrides[stats_api.get_stats_repo] = lambda: EmptyStatsRepo()


# ---------------------------------------------------------------------------
# Tests — conversations
# ---------------------------------------------------------------------------


def test_list_conversations_unauthenticated(client_no_auth: TestClient) -> None:
    response = client_no_auth.get("/api/v1/conversations")
    assert response.status_code == 401


def test_list_conversations_empty(
    client_auth: TestClient, empty_conversations_repo: None
) -> None:
    response = client_auth.get("/api/v1/conversations")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["next_cursor"] is None


def test_list_conversations_returns_row(
    client_auth: TestClient, one_conversation_repo: None
) -> None:
    response = client_auth.get("/api/v1/conversations")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == str(CONVERSATION_ID)
    assert items[0]["question"] == "What?"


def test_list_conversations_limit_out_of_range(
    client_auth: TestClient, empty_conversations_repo: None
) -> None:
    assert client_auth.get("/api/v1/conversations?limit=0").status_code == 422
    assert client_auth.get("/api/v1/conversations?limit=101").status_code == 422


# ---------------------------------------------------------------------------
# Tests — stats
# ---------------------------------------------------------------------------


def test_stats_unauthenticated(client_no_auth: TestClient) -> None:
    response = client_no_auth.get("/api/v1/stats")
    assert response.status_code == 401


def test_stats_returns_zeros_for_new_user(
    client_auth: TestClient, empty_stats_repo: None
) -> None:
    response = client_auth.get("/api/v1/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["conversation_count"] == 0
    assert data["document_count"] == 0
    assert data["refusal_count"] == 0
