import os
from collections.abc import Iterator

import pytest

from explorai_db.testing import temporary_database


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    admin_url = os.environ.get("DATABASE_URL")
    if not admin_url:
        pytest.fail("DATABASE_URL must be set for integration tests")
    with temporary_database(admin_url) as url:
        yield url
