from explorai_db.session import make_engine


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
