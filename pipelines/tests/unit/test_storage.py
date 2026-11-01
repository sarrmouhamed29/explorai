import hashlib
from pathlib import Path

import pytest

from explorai_pipelines.storage import LocalBlobStore


def test_put_get_roundtrip(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    key = store.put(b"hello")
    assert key == hashlib.sha256(b"hello").hexdigest()
    assert store.get(key) == b"hello"


def test_put_is_idempotent(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    assert store.put(b"same") == store.put(b"same")
    assert len([p for p in tmp_path.rglob("*") if p.is_file()]) == 1


@pytest.mark.parametrize("key", ["../x", "abc", "A" * 64, "g" * 64, "a" * 63 + "/"])
def test_invalid_key_rejected(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError, match="key"):
        LocalBlobStore(tmp_path).get(key)


def test_get_missing_blob_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        LocalBlobStore(tmp_path).get("a" * 64)
