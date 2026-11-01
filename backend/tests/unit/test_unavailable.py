"""Slow or vanished peers end a request cleanly: 503 for a dependency, no 500."""

import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.requests import ClientDisconnect

from explorai_backend.api import documents as documents_api
from explorai_backend.api.auth import current_user
from explorai_backend.config import get_settings
from explorai_backend.main import create_app
from explorai_backend.services.auth import PublicUser


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/15")
    monkeypatch.setenv("BLOB_DIR", str(tmp_path))
    get_settings.cache_clear()


def test_pool_timeout_answers_503_like_an_unreachable_database() -> None:
    app = create_app()

    @app.get("/boom")
    def boom() -> None:
        # What SQLAlchemy raises when every pooled connection stays busy.
        raise PoolTimeout("QueuePool limit reached")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


class FakeLimiter:
    limit = 20

    def __init__(self) -> None:
        self.released: list[str] = []

    def hit(self, key: str) -> int:
        return 1

    def release(self, key: str) -> None:
        self.released.append(key)


class NoUploads:
    def upload(self, *args: object) -> None:
        raise AssertionError("nothing to store: the client went away")


USER = PublicUser(id=uuid.uuid4(), email="a@example.org")


def upload_app(limiter: FakeLimiter) -> FastAPI:
    app = create_app()
    user = USER
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[documents_api.get_upload_limiter] = lambda: limiter
    app.dependency_overrides[documents_api.get_uploads_service] = NoUploads
    return app


def test_client_disconnect_mid_upload_ends_quietly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def vanish(_chunks: AsyncIterator[bytes], _cap: int) -> bytes:
        raise ClientDisconnect

    monkeypatch.setattr(documents_api, "read_capped", vanish)
    limiter = FakeLimiter()
    caplog.set_level(logging.INFO)

    # raise_server_exceptions (the default) would re-raise an unhandled error.
    response = TestClient(upload_app(limiter)).post(
        "/api/v1/documents?filename=a.pdf", content=b"%PDF-"
    )

    assert response.status_code == 400
    # The reservation is given back by the route's existing ``finally``.
    assert limiter.released == [str(USER.id)]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not [r for r in caplog.records if r.exc_info]
