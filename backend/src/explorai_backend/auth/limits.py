"""Fixed-window counters in Redis, used to slow down password guessing.

A fixed window counts events from the first one for ``window_seconds``, then
starts over. It is the simplest limiter with O(1) state per key; its weakness
(up to twice the limit across a window boundary) does not matter for login
brute force, where the limit is far below what guessing needs.
"""

from redis import Redis
from redis.exceptions import WatchError


class FixedWindowLimiter:
    def __init__(
        self, redis: Redis, prefix: str, limit: int, window_seconds: int
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._limit = limit
        self._window = window_seconds

    @property
    def limit(self) -> int:
        return self._limit

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    def hit(self, key: str) -> int:
        """Count one event and return the count in the current window.

        INCR is atomic, so concurrent callers each get a distinct count: use the
        returned value to reserve an attempt (allowed when ``<= limit``) rather
        than reading ``exceeded`` first and counting later, which lets a burst
        of parallel requests all pass the check before any of them is counted.
        """
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

    def release(self, key: str) -> None:
        """Give back one reserved event, e.g. an attempt that succeeded.

        Optimistic transaction (WATCH/MULTI): decrement only a counter that still
        exists and is positive, so an expired window is never recreated without
        a TTL. DECR keeps the key's TTL. On a concurrent change the release is
        dropped, which errs on the strict side (one extra count until the
        window ends).
        """
        name = self._key(key)
        with self._redis.pipeline() as pipe:
            try:
                pipe.watch(name)  # type: ignore[no-untyped-call]  # redis-py stub gap
                value = pipe.get(name)
                if value is None or int(value) <= 0:
                    return
                pipe.multi()
                pipe.decr(name)
                pipe.execute()
            except WatchError:
                return

    def reset(self, key: str) -> None:
        self._redis.delete(self._key(key))
