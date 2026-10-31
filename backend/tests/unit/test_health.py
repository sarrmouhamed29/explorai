import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from explorai_backend.api.health import get_checks
from explorai_backend.config import Settings, get_settings
from explorai_backend.main import create_app

Check = Callable[[], Awaitable[bool]]


async def _ok() -> bool:
    return True


async def _down() -> bool:
    return False


async def _hang() -> bool:
    await asyncio.sleep(10)
    return True


async def _boom() -> bool:
    raise RuntimeError("secret://pw")


def _settings(timeout: float = 2.0) -> Settings:
    return Settings(
        app_env="test",
        database_url="postgresql://u:p@localhost:5432/db",  # type: ignore[arg-type]
        redis_url="redis://localhost:6379/0",  # type: ignore[arg-type]
        check_timeout_seconds=timeout,
    )


@pytest.fixture
def make_client() -> Iterator[Callable[[dict[str, Check], float], TestClient]]:
    clients: list[TestClient] = []

    def factory(checks: dict[str, Check], timeout: float = 2.0) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: _settings(timeout)
        app.dependency_overrides[get_checks] = lambda: checks
        client = TestClient(app)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


def test_live_returns_ok(make_client: Callable[..., TestClient]) -> None:
    response = make_client({"postgres": _ok, "redis": _ok}).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_all_healthy(make_client: Callable[..., TestClient]) -> None:
    response = make_client({"postgres": _ok, "redis": _ok}).get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["checks"] == {"postgres": "ok", "redis": "ok"}


def test_ready_postgres_down_marks_only_postgres(
    make_client: Callable[..., TestClient],
) -> None:
    response = make_client({"postgres": _down, "redis": _ok}).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["checks"] == {"postgres": "down", "redis": "ok"}


def test_ready_redis_down_marks_only_redis(
    make_client: Callable[..., TestClient],
) -> None:
    response = make_client({"postgres": _ok, "redis": _down}).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"postgres": "ok", "redis": "down"}


def test_ready_hanging_check_returns_503_within_timeout(
    make_client: Callable[..., TestClient],
) -> None:
    client = make_client({"postgres": _hang, "redis": _ok}, 0.2)
    start = time.monotonic()
    response = client.get("/health/ready")
    elapsed = time.monotonic() - start
    assert response.status_code == 503
    assert response.json()["checks"] == {"postgres": "down", "redis": "ok"}
    assert elapsed < 2


def test_ready_check_raising_is_reported_down(
    make_client: Callable[..., TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("DEBUG"):
        response = make_client({"postgres": _boom, "redis": _ok}).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"postgres": "down", "redis": "ok"}
    assert "secret://pw" not in response.text
    assert "secret://pw" not in caplog.text
    assert "secret://pw" not in "".join(
        str(r.exc_info) for r in caplog.records if r.exc_info
    )
