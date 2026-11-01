from typing import Any

import pytest
import sqlalchemy
from sqlalchemy import Engine

from explorai_db import session
from explorai_db.session import CONNECT_TIMEOUT_SECONDS, make_engine


def test_plain_postgres_url_gets_psycopg_driver() -> None:
    engine = make_engine("postgresql://u:p@127.0.0.1:5432/db")
    assert engine.url.drivername == "postgresql+psycopg"


def test_qualified_url_is_left_alone() -> None:
    engine = make_engine("postgresql+psycopg://u:p@127.0.0.1:5432/db")
    assert engine.url.drivername == "postgresql+psycopg"
    assert engine.url.host == "127.0.0.1"


def test_engine_hides_statement_parameters() -> None:
    # Bound values (a password hash on sign-up) must never reach an error
    # message or a log line.
    engine = make_engine("postgresql://u:p@127.0.0.1:5432/db")
    assert engine.hide_parameters is True


def test_engine_bounds_the_connection_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    # An unreachable server must fail fast (then 503), not hold a worker until
    # the operating system's TCP timeout.
    captured: dict[str, Any] = {}
    real = sqlalchemy.create_engine

    def spy(url: Any, **kwargs: Any) -> Engine:
        captured.update(kwargs)
        return real(url, **kwargs)

    monkeypatch.setattr("explorai_db.session.create_engine", spy)

    engine = session.make_engine("postgresql://u:p@127.0.0.1:5432/db")

    assert captured["connect_args"] == {"connect_timeout": CONNECT_TIMEOUT_SECONDS}
    assert CONNECT_TIMEOUT_SECONDS == 5
    assert captured["pool_pre_ping"] is True
    assert engine.hide_parameters is True
