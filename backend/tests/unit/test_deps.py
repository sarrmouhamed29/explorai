from pathlib import Path

import pytest

from explorai_backend import deps
from explorai_backend.config import get_settings
from explorai_db.blobs import LocalBlobStore


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


def test_session_factory_is_built_lazily_and_cached() -> None:
    factory = deps.get_session_factory()

    assert deps.get_session_factory() is factory
