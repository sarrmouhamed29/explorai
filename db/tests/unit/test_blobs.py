import hashlib
import os
import time
from pathlib import Path

import pytest

from explorai_db.blobs import LocalBlobStore


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


def test_delete_removes_blob(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    key = store.put(b"bye")
    store.delete(key)
    with pytest.raises(FileNotFoundError):
        store.get(key)


def test_delete_missing_key_is_a_no_op(tmp_path: Path) -> None:
    LocalBlobStore(tmp_path).delete("a" * 64)


def test_delete_invalid_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="key"):
        LocalBlobStore(tmp_path).delete("../x")


def test_keys_older_than_skips_recent_blobs(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    old_key = store.put(b"old")
    new_key = store.put(b"new")
    old_time = time.time() - 7200
    os.utime(tmp_path / old_key[:2] / old_key, (old_time, old_time))
    assert store.keys_older_than(3600) == [old_key]
    assert new_key not in store.keys_older_than(3600)


def test_keys_older_than_ignores_temporary_files(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    key = store.put(b"data")
    leftover = tmp_path / key[:2] / "tmpabc123"
    leftover.write_bytes(b"partial")
    old_time = time.time() - 7200
    os.utime(leftover, (old_time, old_time))
    os.utime(tmp_path / key[:2] / key, (old_time, old_time))
    assert store.keys_older_than(3600) == [key]


def test_keys_older_than_on_empty_store(tmp_path: Path) -> None:
    assert LocalBlobStore(tmp_path / "missing").keys_older_than(0) == []
