import os
import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import text

from explorai_db.migrate import alembic_config
from explorai_db.models import Document, Source
from explorai_db.session import make_engine, make_session_factory
from explorai_db.testing import temporary_database

pytestmark = pytest.mark.integration


def test_created_at_is_filled_on_insert(database_url: str) -> None:
    before = datetime.now(UTC)
    factory = make_session_factory(make_engine(database_url))
    with factory.begin() as session:
        source = Source(kind="arxiv", name=f"s-{uuid.uuid4()}", config={})
        session.add(source)
        session.flush()
        document = Document(source_id=source.id, external_id="x", title="t")
        session.add(document)
        session.flush()
        assert document.created_at >= before


def test_backfill_uses_earliest_artifact_time() -> None:
    admin_url = os.environ.get("DATABASE_URL")
    if not admin_url:
        pytest.fail("DATABASE_URL must be set for integration tests")
    with temporary_database(admin_url, migrate=False) as url:
        config = alembic_config(url)
        command.upgrade(config, "0003")
        engine = make_engine(url)
        source_id, doc_id, orphan_id = (uuid.uuid4() for _ in range(3))
        early = datetime(2024, 1, 1, tzinfo=UTC)
        late = datetime(2025, 1, 1, tzinfo=UTC)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO sources (id, kind, name, config)"
                    " VALUES (:i, 'arxiv', 'a', '{}')"
                ),
                {"i": source_id},
            )
            for sha, fetched in (("a" * 64, late), ("b" * 64, early)):
                connection.execute(
                    text(
                        "INSERT INTO raw_artifacts (id, source_id, external_id, url,"
                        " sha256, storage_key, fetched_at, owner_id)"
                        " VALUES (:i, :s, 'doc', 'u', :h, :h, :f, NULL)"
                    ),
                    {"i": uuid.uuid4(), "s": source_id, "h": sha, "f": fetched},
                )
            for i, ext in ((doc_id, "doc"), (orphan_id, "orphan")):
                connection.execute(
                    text(
                        "INSERT INTO documents (id, source_id, external_id, owner_id)"
                        " VALUES (:i, :s, :e, NULL)"
                    ),
                    {"i": i, "s": source_id, "e": ext},
                )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            rows: dict[uuid.UUID, datetime] = dict(
                connection.execute(text("SELECT id, created_at FROM documents")).all()
            )
        assert rows[doc_id] == early
        assert rows[orphan_id] >= late
