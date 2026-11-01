"""Opaque server-side sessions in Redis.

The client holds a random token and nothing else; the server maps it to a user.
Only ``sha256(token)`` is stored, so a dump of Redis yields no token that could
be replayed. A plain hash (no salt, no slow function) is enough here: the token
has 256 bits of entropy, so it cannot be guessed back from its hash, unlike a
password.

Two expiries apply. The idle expiry is the key's TTL, renewed on every use. The
absolute expiry is checked against the creation time stored with the session,
and the renewed TTL never reaches past it, so an active session still ends
``max_seconds`` after login.
"""

import hashlib
import math
import secrets
import time
import uuid
from collections.abc import Callable

from redis import Redis

KEY_PREFIX = "session:"


def _text(value: bytes | str) -> str:
    # The client may or may not be built with decode_responses.
    return value.decode() if isinstance(value, bytes) else value


def session_key(token: str) -> str:
    return KEY_PREFIX + hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    def __init__(
        self,
        redis: Redis,
        idle_seconds: int,
        max_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._idle = idle_seconds
        self._max = max_seconds
        self._clock = clock

    def create(self, user_id: uuid.UUID) -> str:
        """Start a session and return its raw token, to be sent to the client only."""
        token = secrets.token_urlsafe(32)
        key = session_key(token)
        pipe = self._redis.pipeline()
        pipe.hset(key, mapping={"user_id": str(user_id), "created_at": self._clock()})
        pipe.expire(key, min(self._idle, self._max))
        pipe.execute()
        return token

    def resolve(self, token: str) -> uuid.UUID | None:
        """Return the session's user and renew its idle TTL, or ``None``."""
        key = session_key(token)
        record = {_text(k): _text(v) for k, v in self._redis.hgetall(key).items()}
        if not record:
            return None
        try:
            user_id = uuid.UUID(record["user_id"])
            created_at = float(record["created_at"])
        except (KeyError, ValueError):
            self._redis.delete(key)
            return None
        remaining = created_at + self._max - self._clock()
        if remaining <= 0:
            self._redis.delete(key)
            return None
        # EXPIRE XX: renew only if the key still exists, so a session revoked
        # between the read and here is not brought back.
        self._redis.expire(key, min(self._idle, math.ceil(remaining)), xx=True)
        return user_id

    def revoke(self, token: str) -> None:
        self._redis.delete(session_key(token))
