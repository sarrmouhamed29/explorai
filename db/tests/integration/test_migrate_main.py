import os

import pytest
from sqlalchemy import inspect

from explorai_db import migrate
from explorai_db.session import make_engine
from explorai_db.testing import temporary_database

pytestmark = pytest.mark.integration


def test_main_migrates_the_database_named_by_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_url = os.environ["DATABASE_URL"]
    with temporary_database(admin_url, migrate=False) as url:
        monkeypatch.setenv("DATABASE_URL", url)

        migrate.main()

        tables = set(inspect(make_engine(url)).get_table_names())
        assert {"sources", "document_versions"} <= tables


def test_main_without_database_url_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="DATABASE_URL"):
        migrate.main()
