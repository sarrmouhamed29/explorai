from collections.abc import Iterator
from pathlib import Path

import pytest

from explorai_ai.fakes import FakeEmbedder
from explorai_ai.pipeline import AnswerPipeline
from explorai_ai.store import ChunkStore
from explorai_backend import deps
from explorai_backend.config import get_settings
from explorai_db.blobs import LocalBlobStore
from explorai_db.rls import ScopedSessions


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/15")
    monkeypatch.setenv("BLOB_DIR", str(tmp_path))
    for fn in (get_settings, deps.get_session_factory, deps.get_redis):
        fn.cache_clear()
    deps.get_blob_store.cache_clear()


def test_blob_store_is_local_and_cached(tmp_path: Path) -> None:
    store = deps.get_blob_store()

    assert isinstance(store, LocalBlobStore)
    assert deps.get_blob_store() is store
    assert store.get(store.put(b"x")) == b"x"


def test_redis_client_is_built_lazily_and_cached() -> None:
    client = deps.get_redis()

    assert deps.get_redis() is client
    assert client.connection_pool.connection_kwargs["db"] == 15


def test_redis_client_gives_up_on_a_hanging_server() -> None:
    # Without timeouts a stuck Redis hangs the request instead of answering 503.
    kwargs = deps.get_redis().connection_pool.connection_kwargs

    assert kwargs["socket_connect_timeout"] == deps.REDIS_TIMEOUT_SECONDS
    assert kwargs["socket_timeout"] == deps.REDIS_TIMEOUT_SECONDS
    assert deps.REDIS_TIMEOUT_SECONDS == 2


def test_session_factory_is_built_lazily_and_cached() -> None:
    factory = deps.get_session_factory()

    assert deps.get_session_factory() is factory


def test_request_code_gets_scoped_sessions_not_the_factory() -> None:
    sessions = deps.get_scoped_sessions(deps.get_session_factory())

    assert isinstance(sessions, ScopedSessions)


@pytest.fixture
def rag_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RAG_PROVIDER", "fake")
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:1")
    caches = (deps.get_rag_settings, deps.get_qdrant_index, deps.get_providers)
    for fn in caches:
        fn.cache_clear()
    yield
    for fn in caches:
        fn.cache_clear()


def test_rag_clients_are_built_once_without_connecting(
    rag_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[dict[str, object]] = []

    def recording_client(**kwargs: object) -> object:
        built.append(kwargs)
        return object()

    monkeypatch.setattr(deps, "QdrantClient", recording_client)

    assert deps.get_rag_settings() is deps.get_rag_settings()
    assert deps.get_qdrant_index() is deps.get_qdrant_index()
    # One client; its default version check would be a request at construction.
    assert len(built) == 1
    assert built[0]["check_compatibility"] is False
    assert built[0]["url"] == "http://127.0.0.1:1"
    providers = deps.get_providers()

    assert providers is deps.get_providers()
    assert isinstance(providers.embedder, FakeEmbedder)


def test_answer_pipeline_reads_chunks_through_scoped_sessions(rag_env: None) -> None:
    sessions = deps.get_scoped_sessions(deps.get_session_factory())
    store = deps.get_chunk_store(sessions)

    pipeline = deps.get_answer_pipeline(
        deps.get_rag_settings(), store, deps.get_qdrant_index(), deps.get_providers()
    )

    assert isinstance(store, ChunkStore)
    assert isinstance(pipeline, AnswerPipeline)
