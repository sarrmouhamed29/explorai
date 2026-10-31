import logging
from typing import Any

import pytest

from explorai_backend import db
from explorai_backend.db import check_postgres, check_redis

SECRET = "s3cret-pw"
SECRET_URL_POSTGRES = f"postgresql://user:{SECRET}@127.0.0.1:1/db"
SECRET_URL_REDIS = f"redis://:{SECRET}@127.0.0.1:1/0"


# The connect calls are patched to fail instantly with a message that embeds the
# secret, so these tests need no network. Real-network failures are covered by
# the integration tests.
async def _failing_connect(*args: Any, **kwargs: Any) -> Any:
    raise OSError(f"connection to {SECRET_URL_POSTGRES} refused")


async def test_check_postgres_false_on_failure_without_leaking_url(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(db.psycopg.AsyncConnection, "connect", _failing_connect)
    with caplog.at_level(logging.DEBUG):
        assert await check_postgres(SECRET_URL_POSTGRES) is False
    captured = capsys.readouterr()
    assert SECRET not in caplog.text + captured.out + captured.err


async def test_check_redis_false_on_failure_without_leaking_url(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failing_from_url(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError(f"cannot reach {SECRET_URL_REDIS}")

    monkeypatch.setattr(db, "from_url", failing_from_url)
    with caplog.at_level(logging.DEBUG):
        assert await check_redis(SECRET_URL_REDIS) is False
    captured = capsys.readouterr()
    assert SECRET not in caplog.text + captured.out + captured.err
