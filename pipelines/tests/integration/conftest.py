import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from explorai_db.blobs import LocalBlobStore
from explorai_db.session import make_engine, make_session_factory
from explorai_db.testing import temporary_database
from explorai_pipelines.config import PipelineSettings
from explorai_pipelines.repository import Repository


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    admin_url = os.environ.get("DATABASE_URL")
    if not admin_url:
        pytest.fail("DATABASE_URL must be set for integration tests")
    with temporary_database(admin_url) as url:
        yield url


@pytest.fixture
def session_factory(database_url: str) -> sessionmaker[Session]:
    engine = make_engine(database_url)
    with engine.begin() as connection:
        # Every other table references sources, so one cascade empties the schema.
        connection.execute(text("TRUNCATE sources CASCADE"))
    return make_session_factory(engine)


@pytest.fixture
def repo(session_factory: sessionmaker[Session]) -> Repository:
    return Repository(session_factory)


@pytest.fixture
def store(tmp_path: Path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path / "blobs")


@pytest.fixture
def settings(tmp_path: Path) -> PipelineSettings:
    return PipelineSettings(
        database_url="postgresql://u:p@127.0.0.1:5432/db",  # type: ignore[arg-type]
        blob_dir=tmp_path / "blobs",
        contact_email="me@example.org",
        extract_timeout_seconds=60,
    )
