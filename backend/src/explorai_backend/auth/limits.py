"""Fixed-window counters in Redis, used to slow down password guessing.

A fixed window counts events from the first one for ``window_seconds``, then
starts over. It is the simplest limiter with O(1) state per key; its weakness
(up to twice the limit across a window boundary) does not matter for login
brute force, where the limit is far below what guessing needs.
"""

from redis import Redis


class FixedWindowLimiter:
    def __init__(
        self, redis: Redis, prefix: str, limit: int, window_seconds: int
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._limit = limit
        self._window = window_seconds

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    def hit(self, key: str) -> int:
        """Count one event and return the count in the current window."""
        name = self._key(key)
        # One MULTI/EXEC round trip. EXPIRE NX sets the TTL on the first hit only,
        # so later hits do not push the end of the window back.
        pipe = self._redis.pipeline()
        pipe.incr(name)
        pipe.expire(name, self._window, nx=True)
        count, _ = pipe.execute()
        return int(count)

    def exceeded(self, key: str) -> bool:
        value = self._redis.get(self._key(key))
        return value is not None and int(value) >= self._limit

    def reset(self, key: str) -> None:
        self._redis.delete(self._key(key))
