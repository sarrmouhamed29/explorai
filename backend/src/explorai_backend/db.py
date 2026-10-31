"""Connectivity probes for backing services. They never raise and never log DSNs."""

import psycopg
from redis.asyncio import from_url


async def check_postgres(database_url: str) -> bool:
    try:
        conn_cm = await psycopg.AsyncConnection.connect(database_url, connect_timeout=5)
        async with conn_cm as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - probes must never raise
        return False


async def check_redis(redis_url: str) -> bool:
    try:
        client = from_url(redis_url)
        try:
            return bool(await client.ping())
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001 - probes must never raise
        return False
