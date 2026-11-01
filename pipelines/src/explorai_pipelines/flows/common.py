"""Retry policy shared by the network tasks."""

from typing import Any

import httpx

from explorai_pipelines.sources.base import SourceError

RETRIES = 3
RETRY_DELAYS_SECONDS: list[float] = [2.0, 10.0, 30.0]


def is_transient(task: Any, task_run: Any, state: Any) -> bool:
    """Retry only failures that can go away: network errors, 5xx, 429.

    A 404, a refused URL (SSRF policy) or an oversized body will fail the same
    way next time, so retrying them would only waste the rate limit.
    """
    error = state.result(raise_on_failure=False)
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status >= 500 or status == 429
    return isinstance(error, httpx.TransportError | SourceError)
