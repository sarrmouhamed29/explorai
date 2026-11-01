import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from explorai_backend.config import get_settings
from explorai_backend.main import create_app

ALLOWED = "http://127.0.0.1:5173"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BLOB_DIR", "blobs")
    monkeypatch.setenv("ALLOWED_ORIGINS", ALLOWED)
    get_settings.cache_clear()
    app: FastAPI = create_app()

    @app.post("/echo")
    @app.put("/echo")
    @app.patch("/echo")
    @app.delete("/echo")
    @app.get("/echo")
    def echo() -> dict[str, str]:
        return {"ok": "yes"}

    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_foreign_origin_post_rejected(client: TestClient) -> None:
    response = client.post("/echo", headers={"Origin": "http://evil.test"})

    assert response.status_code == 403
    assert response.json() == {"detail": "origin not allowed"}


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_foreign_origin_on_other_unsafe_methods_rejected(
    client: TestClient, method: str
) -> None:
    response = client.request(method, "/echo", headers={"Origin": "http://evil.test"})

    assert response.status_code == 403


def test_allowed_origin_post_passes(client: TestClient) -> None:
    response = client.post("/echo", headers={"Origin": ALLOWED})

    assert response.status_code == 200


def test_missing_origin_passes(client: TestClient) -> None:
    assert client.post("/echo").status_code == 200


def test_get_is_never_checked(client: TestClient) -> None:
    response = client.get("/echo", headers={"Origin": "http://evil.test"})

    assert response.status_code == 200


def test_app_builds_openapi_without_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("APP_ENV", "DATABASE_URL", "REDIS_URL", "BLOB_DIR"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()

    assert create_app().openapi()["info"]["title"] == "Explorai API"
