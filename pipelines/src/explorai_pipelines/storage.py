"""Content-addressed blob storage for raw artifacts."""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

_KEY = re.compile(r"[0-9a-f]{64}")


class BlobStore(Protocol):
    def put(self, data: bytes) -> str:
        """Store bytes and return their key (the SHA-256 hex digest)."""
        ...

    def get(self, key: str) -> bytes: ...


class LocalBlobStore:
    """Blobs on a local volume, fanned out by the first two key characters."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise ValueError("invalid blob key: expected 64 lowercase hex characters")
        return self._root / key[:2] / key

    def put(self, data: bytes) -> str:
        key = hashlib.sha256(data).hexdigest()
        path = self._path(key)
        if path.exists():
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename: a crash never leaves a truncated blob under its key.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            Path(tmp_name).replace(path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()
