from pathlib import Path

import pytest

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
