"""Integration tests: probes and /health/ready against real Postgres and Redis.

Unit tests use fakes; these prove the probes work against actual servers.
Connection settings come from the process environment (never from .env files).
"""

import os
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from explorai_backend.config import get_settings
from explorai_backend.db import check_postgres, check_redis
from explorai_backend.main import create_app

pytestmark = pytest.mark.integration


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"{name} is not set. Start the stack and export the variables from "
            "infra/.env before running integration tests."
        )
    return value


async def test_check_postgres_true_for_real_server() -> None:
    assert await check_postgres(_require_env("DATABASE_URL")) is True


async def test_check_redis_true_for_real_server() -> None:
    assert await check_redis(_require_env("REDIS_URL")) is True


async def test_check_postgres_false_when_port_closed() -> None:
    assert await check_postgres("postgresql://u:p@127.0.0.1:1/db") is False


async def test_check_redis_false_when_port_closed() -> None:
    assert await check_redis("redis://127.0.0.1:1/0") is False


@pytest.fixture
def client() -> Iterator[TestClient]:
    _require_env("DATABASE_URL")
    _require_env("REDIS_URL")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_ready_endpoint_with_real_dependencies_returns_200(client: TestClient) -> None:
    start = time.monotonic()
    response = client.get("/health/ready")
    assert time.monotonic() - start < get_settings().check_timeout_seconds + 1
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"postgres": "ok", "redis": "ok"},
    }
