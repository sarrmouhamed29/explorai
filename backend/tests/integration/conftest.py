"""Integration fixtures: a throwaway migrated database and Redis database 15.

The app under test gets them through ``dependency_overrides``, so these tests
never read or write the developer's own data.
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from qdrant_client.models import DeleteAlias, DeleteAliasOperation
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from explorai_ai.config import RagSettings
from explorai_ai.factory import build_providers
from explorai_ai.index import QdrantIndex
from explorai_backend import deps
from explorai_backend.config import Settings, get_settings
from explorai_backend.main import create_app
from explorai_db.blobs import LocalBlobStore
from explorai_db.session import make_engine, make_session_factory
from explorai_db.testing import temporary_database

TEST_REDIS_DB = 15
# Small vectors: the fake embedder hashes words into this many buckets.
TEST_DIMENSION = 16


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
def rag_settings() -> RagSettings:
    """Fake models, and an embedding model name of its own for each test.

    The index name (``chunks_fake_<model>_<dimension>``) keys both the Qdrant
    collection and the ``chunk_index_entries`` rows, so tests never share one.
    """
    return RagSettings(
        provider="fake",
        embedding_model=f"t{uuid.uuid4().hex[:8]}",
        embedding_dimension=TEST_DIMENSION,
    )


@pytest.fixture
def app(
    settings: Settings,
    rag_settings: RagSettings,
    session_factory: sessionmaker[Session],
    redis_client: redis.Redis,
    clean_database: None,
) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[deps.get_rag_settings] = lambda: rag_settings
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


@pytest.fixture
def qdrant_client() -> Iterator[QdrantClient]:
    """A client of the Qdrant under test (QDRANT_URL, QDRANT_API_KEY if required)."""
    qdrant = QdrantClient(
        url=require_env("QDRANT_URL"), api_key=os.environ.get("QDRANT_API_KEY")
    )
    yield qdrant
    qdrant.close()


@pytest.fixture
def qdrant_index(
    qdrant_client: QdrantClient, rag_settings: RagSettings
) -> Iterator[QdrantIndex]:
    """An index behind an alias of its own; the alias and collection are dropped."""
    alias = f"test_backend_{uuid.uuid4().hex[:8]}"
    index = QdrantIndex(qdrant_client, alias=alias)
    yield index
    if index.alias_target() is not None:
        qdrant_client.update_collection_aliases(
            change_aliases_operations=[
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=alias))
            ]
        )
    if index.collection_exists(rag_settings.index_name):
        qdrant_client.delete_collection(rag_settings.index_name)


@pytest.fixture
def rag_app(
    app: FastAPI, rag_settings: RagSettings, qdrant_index: QdrantIndex
) -> FastAPI:
    """``app`` answering from the test index with the fake models.

    It changes the overrides of the same app object, so the ``client`` fixture
    (built on ``app``) talks to it.
    """
    app.dependency_overrides[deps.get_qdrant_index] = lambda: qdrant_index
    providers = build_providers(rag_settings)
    app.dependency_overrides[deps.get_providers] = lambda: providers
    return app
