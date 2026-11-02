"""Connectivity probes for backing services. They never raise and never log DSNs."""

import psycopg
from redis.asyncio import from_url


async def check_postgres(database_url: str) -> bool:
    try:
        # connect_timeout only bounds the standalone probe. On the /health/ready route
        # the effective bound is asyncio.wait_for(check_timeout_seconds, default 2.0 s),
        # which is shorter.
        conn_cm = await psycopg.AsyncConnection.connect(database_url, connect_timeout=5)
        async with conn_cm as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - probes must never raise
        return False


async def check_redis(redis_url: str) -> bool:
    try:
        # Socket timeouts are defence in depth: the caller bounds the probe, but a
        # stuck socket must not outlive it (same 5 s bound as connect_timeout above).
        client = from_url(redis_url, socket_connect_timeout=5, socket_timeout=5)
        try:
            return bool(await client.ping())
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001 - probes must never raise
        return False
