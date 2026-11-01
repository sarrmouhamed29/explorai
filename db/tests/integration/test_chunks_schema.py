"""Migration 0005: chunk tables, their Row-Level Security and their cascades.

As in ``test_rls``, fixtures are inserted as the table owner (who bypasses RLS)
and every assertion about the API role goes through ``scoped_session``.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from alembic import command
from sqlalchemy import CursorResult, Engine, inspect, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from explorai_db.migrate import alembic_config
from explorai_db.models import (
    Chunk,
    ChunkIndexEntry,
    Document,
    DocumentVersion,
    RawArtifact,
    Source,
    User,
)
from explorai_db.rls import scoped_session
from explorai_db.session import make_engine, make_session_factory
from explorai_db.testing import temporary_database

pytestmark = pytest.mark.integration

CHUNK_TABLES = {"chunks", "chunk_index_entries"}
MODEL = "test-embedding"


@dataclass(frozen=True)
class Owned:
    """One document, its version and the chunk (with one index entry) under it."""

    document: uuid.UUID
    version: uuid.UUID
    chunk: uuid.UUID


@dataclass(frozen=True)
class World:
    user_a: uuid.UUID
    user_b: uuid.UUID
    public: Owned
    a: Owned
    b: Owned


@pytest.fixture(scope="module")
def chunks_url(database_url: str) -> Iterator[str]:
    # A database of its own, so raw unfiltered queries see only this module's rows.
    with temporary_database(database_url) as url:
        yield url


@pytest.fixture
def engine(chunks_url: str) -> Engine:
    engine = make_engine(chunks_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE users, documents, raw_artifacts, document_versions, "
                "sections, chunks, chunk_index_entries CASCADE"
            )
        )
    return engine


@pytest.fixture
def factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


def add_user(session: Session, name: str) -> uuid.UUID:
    user = User(email=f"{name}-{uuid.uuid4().hex[:8]}@example.org", password_hash="x")
    session.add(user)
    session.flush()
    return user.id


def add_chunked_document(
    session: Session, source_id: uuid.UUID, owner_id: uuid.UUID | None
) -> Owned:
    external_id = uuid.uuid4().hex
    document = Document(source_id=source_id, external_id=external_id, owner_id=owner_id)
    artifact = RawArtifact(
        source_id=source_id,
        external_id=external_id,
        url=f"https://example.org/{external_id}",
        sha256=external_id.ljust(64, "0"),
        storage_key=external_id.ljust(64, "0"),
        owner_id=owner_id,
    )
    session.add_all([document, artifact])
    session.flush()
    version = DocumentVersion(
        document_id=document.id,
        artifact_id=artifact.id,
        extractor_version="pdf-1",
        status="ok",
    )
    session.add(version)
    session.flush()
    chunk = Chunk(
        version_id=version.id,
        ordinal=0,
        text="chunk text",
        word_count=2,
        text_hash="d" * 64,
    )
    session.add(chunk)
    session.flush()
    session.add(ChunkIndexEntry(chunk_id=chunk.id, embedding_model=MODEL))
    session.flush()
    return Owned(document.id, version.id, chunk.id)


@pytest.fixture
def world(factory: sessionmaker[Session]) -> World:
    with factory.begin() as session:
        source = Source(kind="test", name=f"chunks-{uuid.uuid4()}", config={})
        session.add(source)
        session.flush()
        user_a, user_b = add_user(session, "a"), add_user(session, "b")
        return World(
            user_a=user_a,
            user_b=user_b,
            public=add_chunked_document(session, source.id, None),
            a=add_chunked_document(session, source.id, user_a),
            b=add_chunked_document(session, source.id, user_b),
        )


def ids(session: Session, query: str) -> set[uuid.UUID]:
    return set(session.scalars(text(query)).all())


VERSIONS_INDEX = "ix_document_versions_document_status_created"


def version_index_definitions(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return list(
            connection.scalars(
                text(
                    "SELECT indexdef FROM pg_indexes"
                    " WHERE tablename = 'document_versions' AND indexname = :name"
                ),
                {"name": VERSIONS_INDEX},
            )
        )


def test_versions_index_follows_the_migration(database_url: str) -> None:
    with temporary_database(database_url) as url:
        config = alembic_config(url)
        engine = make_engine(url)
        at_head = version_index_definitions(engine)
        command.downgrade(config, "0004")
        after_downgrade = version_index_definitions(engine)
        engine.dispose()
    assert len(at_head) == 1
    assert "(document_id, status, created_at, id)" in at_head[0]
    assert after_downgrade == []


def test_migration_round_trip(database_url: str) -> None:
    with temporary_database(database_url) as url:
        config = alembic_config(url)
        engine = make_engine(url)

        command.downgrade(config, "0004")
        inspector = inspect(engine)
        assert not CHUNK_TABLES & set(inspector.get_table_names())
        columns = {c["name"] for c in inspector.get_columns("document_versions")}
        assert "chunked_at" not in columns

        command.upgrade(config, "head")
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) >= CHUNK_TABLES
        columns = {c["name"] for c in inspector.get_columns("document_versions")}
        assert "chunked_at" in columns
        with engine.connect() as connection:
            policies = set(
                connection.execute(
                    text(
                        "SELECT tablename, policyname, cmd FROM pg_policies"
                        " WHERE tablename IN ('chunks', 'chunk_index_entries')"
                    )
                ).tuples()
            )
            secured = set(
                connection.execute(
                    text(
                        "SELECT relname, relrowsecurity, relforcerowsecurity"
                        " FROM pg_class"
                        " WHERE relname IN ('chunks', 'chunk_index_entries')"
                    )
                ).tuples()
            )
        engine.dispose()
    assert policies == {
        ("chunks", "chunks_select", "SELECT"),
        ("chunk_index_entries", "chunk_index_entries_select", "SELECT"),
    }
    # Enabled, not forced: the owner (pipeline) must keep bypassing RLS.
    assert secured == {
        ("chunks", True, False),
        ("chunk_index_entries", True, False),
    }


def test_app_role_reads_only_visible_chunks(
    factory: sessionmaker[Session], world: World
) -> None:
    with scoped_session(factory, world.user_a) as session:
        chunks = ids(session, "SELECT id FROM chunks")
        entries = ids(session, "SELECT chunk_id FROM chunk_index_entries")
    assert chunks == {world.public.chunk, world.a.chunk}
    assert entries == {world.public.chunk, world.a.chunk}


def test_no_identity_reads_public_chunks_only(
    factory: sessionmaker[Session], world: World
) -> None:
    with scoped_session(factory, None) as session:
        chunks = ids(session, "SELECT id FROM chunks")
        entries = ids(session, "SELECT chunk_id FROM chunk_index_entries")
    assert chunks == {world.public.chunk}
    assert entries == {world.public.chunk}


def test_app_role_can_read_chunked_at(
    factory: sessionmaker[Session], world: World
) -> None:
    with scoped_session(factory, world.user_a) as session:
        value = session.scalar(
            text("SELECT chunked_at FROM document_versions WHERE id = :id"),
            {"id": world.a.version},
        )
    assert value is None


WRITES = [
    (
        "INSERT INTO chunks (id, version_id, ordinal, text, word_count, text_hash)"
        " VALUES (gen_random_uuid(), :version, 99, 'forged', 1, repeat('e', 64))"
    ),
    "UPDATE chunks SET text = 'forged' WHERE id = :chunk",
    "DELETE FROM chunks WHERE id = :chunk",
    (
        "INSERT INTO chunk_index_entries (chunk_id, embedding_model, indexed_at)"
        " VALUES (:chunk, 'forged-model', now())"
    ),
    "UPDATE chunk_index_entries SET indexed_at = now() WHERE chunk_id = :chunk",
    "DELETE FROM chunk_index_entries WHERE chunk_id = :chunk",
]


@pytest.mark.parametrize(
    "statement",
    WRITES,
    ids=[
        "insert-chunk",
        "update-chunk",
        "delete-chunk",
        "insert-entry",
        "update-entry",
        "delete-entry",
    ],
)
def test_app_role_cannot_write_chunks(
    factory: sessionmaker[Session], world: World, statement: str
) -> None:
    # Even on the user's own rows: the API role has SELECT only, so the
    # privilege check fails before any policy is consulted.
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        scoped_session(factory, world.user_a) as session,
    ):
        session.execute(
            text(statement), {"version": world.a.version, "chunk": world.a.chunk}
        )
    with factory() as session:
        chunk = session.get(Chunk, world.a.chunk)
        assert chunk is not None
        assert chunk.text == "chunk text"
        entries = session.scalars(
            text("SELECT embedding_model FROM chunk_index_entries WHERE chunk_id = :c"),
            {"c": world.a.chunk},
        ).all()
    assert entries == [MODEL]


def test_deleting_a_version_cascades_to_chunks_and_entries(
    factory: sessionmaker[Session], world: World
) -> None:
    # The pipeline path: the owner deletes a version.
    with factory.begin() as session:
        result = session.execute(
            text("DELETE FROM document_versions WHERE id = :id"),
            {"id": world.public.version},
        )
        assert isinstance(result, CursorResult)
        assert result.rowcount == 1
    # The API path: the user deletes their document; the cascade runs as the
    # table owner, so no DELETE grant on chunks is needed.
    with scoped_session(factory, world.user_a) as session:
        result = session.execute(
            text("DELETE FROM documents WHERE id = :id"), {"id": world.a.document}
        )
        assert isinstance(result, CursorResult)
        assert result.rowcount == 1
    with factory() as session:
        chunks = ids(session, "SELECT id FROM chunks")
        entries = ids(session, "SELECT chunk_id FROM chunk_index_entries")
    assert chunks == {world.b.chunk}
    assert entries == {world.b.chunk}
