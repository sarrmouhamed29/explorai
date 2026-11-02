"""Tests: pii_action query param on upload, pii_blocked in document responses."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from explorai_backend import deps
from explorai_backend.api import documents as docs_api
from explorai_backend.api.auth import current_user
from explorai_backend.config import get_settings
from explorai_backend.main import create_app
from explorai_backend.repositories.documents import DocumentRow
from explorai_backend.services.auth import PublicUser
from explorai_backend.services.documents import DocumentsPage, Upload

DOCS = "/api/v1/documents"
USER = PublicUser(id=uuid.uuid4(), email="u@example.org")
_DOC_ID = uuid.uuid4()
_MINIMAL_PDF = b"%PDF-1.4\n"


def _row(pii_blocked: bool = False) -> DocumentRow:
    return DocumentRow(
        id=_DOC_ID,
        title="Test",
        source_kind="upload",
        owned=True,
        status="processing",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        pii_blocked=pii_blocked,
    )


class _Limiter:
    limit = 10

    def hit(self, key: str) -> int:
        return 1

    def release(self, key: str) -> None:
        pass


class _UploadSvc:
    def __init__(self) -> None:
        self.received_pii_action: list[str] = []

    def upload(
        self,
        user_id: uuid.UUID,
        data: bytes,
        filename: str | None,
        pii_action: str = "block",
    ) -> Upload:
        self.received_pii_action.append(pii_action)
        return Upload(document=_row(), created=True)


class _DocsSvc:
    def list(
        self,
        user_id: uuid.UUID,
        scope: str,
        limit: int,
        after: object,
    ) -> DocumentsPage:
        return DocumentsPage(items=[_row()], next_after=None)


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


def _upload_app(svc: _UploadSvc | None = None) -> FastAPI:
    svc = svc or _UploadSvc()
    app = create_app()
    app.dependency_overrides[current_user] = lambda: USER
    app.dependency_overrides[docs_api.get_uploads_service] = lambda: svc
    app.dependency_overrides[docs_api.get_upload_limiter] = lambda: _Limiter()
    return app


def _list_app() -> FastAPI:
    app = create_app()
    app.dependency_overrides[current_user] = lambda: USER
    app.dependency_overrides[docs_api.get_documents_service] = lambda: _DocsSvc()
    return app


def test_upload_default_pii_action_response_has_pii_blocked() -> None:
    response = TestClient(_upload_app(), raise_server_exceptions=False).post(
        DOCS, content=_MINIMAL_PDF
    )
    assert response.status_code in (200, 202)
    assert response.json()["pii_blocked"] is False


def test_upload_redact_pii_action_is_forwarded_to_service() -> None:
    svc = _UploadSvc()
    TestClient(_upload_app(svc), raise_server_exceptions=False).post(
        DOCS + "?pii_action=redact", content=_MINIMAL_PDF
    )
    assert svc.received_pii_action == ["redact"]


def test_upload_invalid_pii_action_is_422() -> None:
    response = TestClient(_upload_app(), raise_server_exceptions=False).post(
        DOCS + "?pii_action=unknown", content=_MINIMAL_PDF
    )
    assert response.status_code == 422


def test_document_list_exposes_pii_blocked() -> None:
    response = TestClient(_list_app(), raise_server_exceptions=False).get(DOCS)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["pii_blocked"] is False
