import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from explorai_db.session import make_engine

pytestmark = pytest.mark.integration


def test_statement_errors_do_not_carry_bound_values(database_url: str) -> None:
    secret = "argon2id-hash-that-must-not-leak"
    engine = make_engine(database_url)
    try:
        with engine.connect() as connection, pytest.raises(DBAPIError) as caught:
            # Division by zero: PostgreSQL's own message does not quote the value,
            # so only SQLAlchemy's "[parameters: ...]" suffix could show it.
            connection.execute(
                text("SELECT CAST(:value AS text), 1 / 0"), {"value": secret}
            )
    finally:
        engine.dispose()

    assert secret not in str(caught.value)
    assert "parameters hidden" in str(caught.value)
