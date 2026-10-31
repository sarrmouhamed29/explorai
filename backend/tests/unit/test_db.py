import logging

import pytest

from explorai_backend.db import check_postgres, check_redis

SECRET_URL_POSTGRES = "postgresql://user:s3cret-pw@127.0.0.1:1/db"
SECRET_URL_REDIS = "redis://:s3cret-pw@127.0.0.1:1/0"


async def test_check_postgres_false_on_failure_without_leaking_url(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert await check_postgres(SECRET_URL_POSTGRES) is False
    captured = capsys.readouterr()
    assert "s3cret-pw" not in caplog.text + captured.out + captured.err


async def test_check_redis_false_on_failure_without_leaking_url(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert await check_redis(SECRET_URL_REDIS) is False
    captured = capsys.readouterr()
    assert "s3cret-pw" not in caplog.text + captured.out + captured.err
