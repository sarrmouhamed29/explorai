"""Integration fixtures: a throwaway migrated database and Redis database 15.

The app under test gets them through ``dependency_overrides``, so these tests
never read or write the developer's own data.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from explorai_backend import deps
from explorai_backend.config import Settings, get_settings
from explorai_backend.main import create_app
from explorai_db.blobs import LocalBlobStore
from explorai_db.session import make_engine, make_session_factory
from explorai_db.testing import temporary_database

TEST_REDIS_DB = 15


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"{name} is not set. Start the stack and export the variables from "
            "infra/.env before running integration tests."
        )
    return value


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with temporary_database(require_env("DATABASE_URL")) as url:
        yield url


@pytest.fixture(scope="session")
def session_factory(database_url: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(database_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def clean_database(session_factory: sessionmaker[Session]) -> None:
    """Start each test without accounts (as the owner, so RLS does not apply)."""
    with session_factory.begin() as session:
        session.execute(text("TRUNCATE users CASCADE"))


@pytest.fixture(scope="session")
def test_redis_url() -> str:
    parts = urlsplit(require_env("REDIS_URL"))
    return urlunsplit(parts._replace(path=f"/{TEST_REDIS_DB}"))


@pytest.fixture
def redis_client(test_redis_url: str) -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(test_redis_url)
    assert client.connection_pool.connection_kwargs["db"] == TEST_REDIS_DB
    client.flushdb()
    yield client
    client.close()


@pytest.fixture
def settings(tmp_path: Path, database_url: str, test_redis_url: str) -> Settings:
    return Settings.from_env().model_copy(
        update={
            "database_url": database_url,
            "redis_url": test_redis_url,
            "blob_dir": tmp_path,
            "session_cookie_secure": True,
        }
    )


@pytest.fixture
def app(
    settings: Settings,
    session_factory: sessionmaker[Session],
    redis_client: redis.Redis,
    clean_database: None,
) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[deps.get_session_factory] = lambda: session_factory
    app.dependency_overrides[deps.get_redis] = lambda: redis_client
    app.dependency_overrides[deps.get_blob_store] = lambda: LocalBlobStore(
        settings.blob_dir
    )
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # https: the session cookie is Secure, and the client only sends it back over
    # https, as a browser would.
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
