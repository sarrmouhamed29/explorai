import os
from collections.abc import Iterator

import pytest
from qdrant_client import QdrantClient

from explorai_db.testing import temporary_database


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A throwaway database migrated to head, dropped after the session."""
    admin_url = os.environ.get("DATABASE_URL")
    if not admin_url:
        pytest.fail("DATABASE_URL must be set for integration tests")
    with temporary_database(admin_url) as url:
        yield url


@pytest.fixture
def client() -> Iterator[QdrantClient]:
    """A client of the Qdrant under test (QDRANT_URL / QDRANT_API_KEY)."""
    url = os.environ.get("QDRANT_URL")
    if not url:
        pytest.fail("QDRANT_URL must be set for integration tests")
    qdrant = QdrantClient(url=url, api_key=os.environ.get("QDRANT_API_KEY"))
    yield qdrant
    qdrant.close()
