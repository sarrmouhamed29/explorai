"""Content-addressed blob storage shared by the API and the pipeline."""

import hashlib
import os
import re
import tempfile
import time
import uuid
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

    def delete_if_older_than(self, key: str, seconds: float) -> bool:
        """Delete a blob unless it was written within ``seconds``; True if deleted.

        Unlike ``delete``, this is safe against a concurrent ``put`` of the same
        bytes that refreshed the blob after it was listed as old.
        """
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
            # Refresh the age: a garbage collector must not delete an old orphan
            # blob that this upload has just referenced again.
            try:
                os.utime(path)
                return key
            except FileNotFoundError:
                pass  # collected between the check and now: write it again
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

    def delete_if_older_than(self, key: str, seconds: float) -> bool:
        path = self._path(key)
        # Moving the blob aside is atomic: from now on a concurrent put either
        # refreshed it before the move (we see the new mtime and restore it) or
        # finds it missing and writes it again.
        aside = path.with_name(f"{key}.gc-{uuid.uuid4().hex}")
        try:
            path.rename(aside)
        except FileNotFoundError:
            return False
        if aside.stat().st_mtime >= time.time() - seconds:
            aside.replace(path)
            return False
        aside.unlink()
        return True

    def keys_older_than(self, seconds: float) -> list[str]:
        cutoff = time.time() - seconds
        keys: list[str] = []
        for path in self._root.glob("??/*"):
            # Names that are not keys are temporary files of an interrupted write.
            if not _KEY.fullmatch(path.name):
                continue
            try:
                modified = path.stat().st_mtime
            except FileNotFoundError:
                continue  # deleted between listing and stat
            if modified < cutoff:
                keys.append(path.name)
        return sorted(keys)
