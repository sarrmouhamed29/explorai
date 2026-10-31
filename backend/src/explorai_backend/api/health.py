"""Liveness (process is up) and readiness (dependencies reachable) endpoints."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from explorai_backend.config import Settings, get_settings
from explorai_backend.db import check_postgres, check_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

Check = Callable[[], Awaitable[bool]]


class LiveResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, Literal["ok", "down"]]


def get_checks(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Check]:
    return {
        "postgres": partial(check_postgres, str(settings.database_url)),
        "redis": partial(check_redis, str(settings.redis_url)),
    }


async def _run_check(name: str, check: Check, timeout: float) -> bool:
    try:
        return bool(await asyncio.wait_for(check(), timeout))
    except TimeoutError:
        logger.warning("health check %s timed out", name)
    except Exception:  # noqa: BLE001 - any failure means "down"
        # Deliberately no exception text/traceback: it may contain connection secrets.
        logger.warning("health check %s failed", name)
    return False


@router.get("/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    return LiveResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse}},
)
async def ready(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    checks: Annotated[dict[str, Check], Depends(get_checks)],
) -> ReadyResponse:
    names = list(checks)
    results = await asyncio.gather(
        *(_run_check(n, checks[n], settings.check_timeout_seconds) for n in names)
    )
    statuses: dict[str, Literal["ok", "down"]] = {
        n: "ok" if r else "down" for n, r in zip(names, results, strict=True)
    }
    healthy = all(r for r in results)
    if not healthy:
        response.status_code = 503
    return ReadyResponse(status="ok" if healthy else "unavailable", checks=statuses)
