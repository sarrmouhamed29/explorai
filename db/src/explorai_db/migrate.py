"""Programmatic Alembic helpers (the CLI uses ``db/alembic.ini``)."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_to_head(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")


def downgrade_to_base(database_url: str) -> None:
    command.downgrade(alembic_config(database_url), "base")


def main() -> None:
    """``python -m explorai_db.migrate``: upgrade the database in ``DATABASE_URL``."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    upgrade_to_head(url)


if __name__ == "__main__":
    main()
