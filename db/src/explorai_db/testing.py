"""Test support shared by the packages that need a real, migrated database."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from explorai_db.migrate import upgrade_to_head


@contextmanager
def temporary_database(admin_url: str, *, migrate: bool = True) -> Iterator[str]:
    """Create a throwaway database next to ``admin_url``'s, drop it on exit.

    Yields a ``postgresql://`` URL for the new database, migrated to head
    unless ``migrate`` is false.
    """
    name = f"explorai_test_{uuid.uuid4().hex[:12]}"
    admin = make_url(admin_url).set(drivername="postgresql")
    new_url = admin.set(database=name)
    conninfo = admin.render_as_string(hide_password=False)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = new_url.render_as_string(hide_password=False)
    try:
        if migrate:
            upgrade_to_head(url)
        yield url
    finally:
        with psycopg.connect(conninfo, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(name)
                )
            )
