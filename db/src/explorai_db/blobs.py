"""Content-addressed blob storage shared by the API and the pipeline."""

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Protocol

_KEY = re.compile(r"[0-9a-f]{64}")


class BlobStore(Protocol):
    def put(self, data: bytes) -> str:
        """Store bytes and return their key (the SHA-256 hex digest)."""
        ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None:
        """Remove a blob; a missing key is not an error."""
        ...

    def keys_older_than(self, seconds: float) -> list[str]:
        """Keys of blobs last written more than ``seconds`` ago."""
        ...


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

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def keys_older_than(self, seconds: float) -> list[str]:
        cutoff = time.time() - seconds
        keys: list[str] = []
        for path in self._root.glob("??/*"):
            # Names that are not keys are temporary files of an interrupted write.
            if _KEY.fullmatch(path.name) and path.stat().st_mtime < cutoff:
                keys.append(path.name)
        return sorted(keys)
