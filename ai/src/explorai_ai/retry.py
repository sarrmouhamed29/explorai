"""Retry only what is transient: a bad request fails the same way every time."""

import time
from collections.abc import Callable, Sequence

from explorai_ai.errors import ProviderUnavailable


def retry_transient[T](
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    delays: Sequence[float] = (0.5, 2.0),
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call fn, retrying ProviderUnavailable (incl. rate limits) up to `attempts` times.

    The last delay is reused if there are more retries than delays. Any other
    exception propagates immediately; after the last attempt the last error is raised.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except ProviderUnavailable:
            if attempt >= attempts:
                raise
            if delays:
                sleep(delays[min(attempt - 1, len(delays) - 1)])
