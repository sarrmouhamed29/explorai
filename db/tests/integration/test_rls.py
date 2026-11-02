"""Row-Level Security: what the API role sees and may change.

Fixtures are inserted as the table owner, which bypasses RLS (it is enabled
without ``FORCE``); every assertion then goes through ``scoped_session``, which
switches to ``explorai_app`` and sets the identity for one transaction.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import CursorResult, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from explorai_db.models import (
    Document,
    DocumentVersion,
    RawArtifact,
    Section,
    Source,
    User,
)
from explorai_db.rls import APP_ROLE, scoped_session
from explorai_db.session import make_engine, make_session_factory
from explorai_db.testing import temporary_database

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class Owned:
    """The ids of one document and the rows that hang off it."""

    document: uuid.UUID
    artifact: uuid.UUID
    version: uuid.UUID
    section: uuid.UUID


@dataclass(frozen=True)
class World:
    source: uuid.UUID
    user_a: uuid.UUID
    user_b: uuid.UUID
    public: Owned
    a: Owned
    b: Owned


@pytest.fixture(scope="module")
def rls_url(database_url: str) -> Iterator[str]:
    # A database of its own, so raw unfiltered queries see only this module's rows.
    with temporary_database(database_url) as url:
        yield url


@pytest.fixture
def engine(rls_url: str) -> Engine:
    engine = make_engine(rls_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE users, documents, raw_artifacts, document_versions, "
                "sections CASCADE"
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


def add_document(
    session: Session,
    source_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    *,
    duplicate_of: uuid.UUID | None = None,
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
        duplicate_of=duplicate_of,
    )
    session.add(version)
    session.flush()
    section = Section(version_id=version.id, ordinal=0, text="text")
    session.add(section)
    session.flush()
    return Owned(document.id, artifact.id, version.id, section.id)


@pytest.fixture
def world(factory: sessionmaker[Session]) -> World:
    with factory.begin() as session:
        source = Source(kind="test", name=f"rls-{uuid.uuid4()}", config={})
        session.add(source)
        session.flush()
        user_a, user_b = add_user(session, "a"), add_user(session, "b")
        return World(
            source=source.id,
            user_a=user_a,
            user_b=user_b,
            public=add_document(session, source.id, None),
            a=add_document(session, source.id, user_a),
            b=add_document(session, source.id, user_b),
        )


def ids(session: Session, query: str) -> set[uuid.UUID]:
    return set(session.scalars(text(query)).all())


def delete(session: Session, table: str, row_ids: list[uuid.UUID]) -> int:
    """Delete ``row_ids`` from ``table`` and return how many rows went."""
    result = session.execute(
        text(f"DELETE FROM {table} WHERE id = ANY(:ids)"), {"ids": row_ids}
    )
    assert isinstance(result, CursorResult)
    return result.rowcount


def test_owner_sees_own_and_public_only(
    factory: sessionmaker[Session], world: World
) -> None:
    with scoped_session(factory, world.user_a) as session:
        documents = ids(session, "SELECT id FROM documents")
        artifacts = ids(session, "SELECT id FROM raw_artifacts")
    assert documents == {world.public.document, world.a.document}
    assert artifacts == {world.public.artifact, world.a.artifact}


def test_no_identity_sees_public_only(
    factory: sessionmaker[Session], world: World
) -> None:
    with scoped_session(factory, None) as session:
        documents = ids(session, "SELECT id FROM documents")
        artifacts = ids(session, "SELECT id FROM raw_artifacts")
        versions = ids(session, "SELECT id FROM document_versions")
        sections = ids(session, "SELECT id FROM sections")
    assert documents == {world.public.document}
    assert artifacts == {world.public.artifact}
    assert versions == {world.public.version}
    assert sections == {world.public.section}


def test_versions_and_sections_follow_their_document(
    factory: sessionmaker[Session], world: World
) -> None:
    with scoped_session(factory, world.user_a) as session:
        versions = ids(session, "SELECT id FROM document_versions")
        sections = ids(session, "SELECT id FROM sections")
    assert versions == {world.public.version, world.a.version}
    assert sections == {world.public.section, world.a.section}


INSERT_DOCUMENT = (
    "INSERT INTO documents (id, source_id, external_id, owner_id) "
    "VALUES (:id, :source, :external_id, :owner)"
)
INSERT_ARTIFACT = (
    "INSERT INTO raw_artifacts "
    "(id, source_id, external_id, url, sha256, storage_key, fetched_at, owner_id) "
    "VALUES (:id, :source, :external_id, 'https://example.org/u', "
    "repeat('c', 64), repeat('c', 64), now(), :owner)"
)


@pytest.mark.parametrize("statement", [INSERT_DOCUMENT, INSERT_ARTIFACT])
@pytest.mark.parametrize("owner", ["b", "public"])
def test_cannot_insert_for_another_owner(
    factory: sessionmaker[Session], world: World, statement: str, owner: str
) -> None:
    params = {
        "id": uuid.uuid4(),
        "source": world.source,
        "external_id": "forged",
        "owner": world.user_b if owner == "b" else None,
    }
    with (
        pytest.raises(ProgrammingError, match="row-level security"),
        scoped_session(factory, world.user_a) as session,
    ):
        session.execute(text(statement), params)


def test_owner_can_insert_and_delete_own_rows(
    factory: sessionmaker[Session], world: World
) -> None:
    document_id, artifact_id = uuid.uuid4(), uuid.uuid4()
    with scoped_session(factory, world.user_a) as session:
        for statement, row_id in (
            (INSERT_DOCUMENT, document_id),
            (INSERT_ARTIFACT, artifact_id),
        ):
            session.execute(
                text(statement),
                {
                    "id": row_id,
                    "source": world.source,
                    "external_id": "upload",
                    "owner": world.user_a,
                },
            )
    with scoped_session(factory, world.user_a) as session:
        # Deleting the document cascades to its versions and their sections.
        assert delete(session, "documents", [document_id, world.a.document]) == 2
        assert delete(session, "raw_artifacts", [artifact_id, world.a.artifact]) == 2
    with factory() as session:
        assert session.get(DocumentVersion, world.a.version) is None
        assert session.get(Section, world.a.section) is None


@pytest.mark.parametrize(
    ("table", "attribute"),
    [
        ("documents", "document"),
        ("raw_artifacts", "artifact"),
        ("document_versions", "version"),
        ("sections", "section"),
    ],
)
def test_cannot_delete_public_or_foreign_rows(
    factory: sessionmaker[Session], world: World, table: str, attribute: str
) -> None:
    targets = [getattr(world.public, attribute), getattr(world.b, attribute)]
    with scoped_session(factory, world.user_a) as session:
        assert delete(session, table, targets) == 0
    with factory() as session:
        remaining = session.scalars(
            text(f"SELECT id FROM {table} WHERE id = ANY(:ids)"),
            {"ids": targets},
        ).all()
    assert set(remaining) == set(targets)


@pytest.mark.parametrize("fails", [False, True])
def test_identity_and_role_do_not_survive_the_transaction(
    rls_url: str, world: World, fails: bool
) -> None:
    # One pooled connection: the plain session below reuses the scoped one's.
    engine = create_engine(make_engine(rls_url).url, pool_size=1, max_overflow=0)
    factory = make_session_factory(engine)
    with factory() as session:
        owner = session.scalar(text("SELECT current_user"))

    class Boom(Exception):
        pass

    try:
        with scoped_session(factory, world.user_a) as session:
            assert session.scalar(text("SELECT current_user")) == APP_ROLE
            assert session.scalar(text("SELECT app_user()")) == world.user_a
            if fails:
                raise Boom
    except Boom:
        pass

    with factory() as session:
        assert session.scalar(text("SELECT current_user")) == owner
        assert session.scalar(text("SELECT app_user()")) is None
    assert owner != APP_ROLE
    engine.dispose()


def test_app_role_cannot_read_ingestion_runs(
    factory: sessionmaker[Session], world: World
) -> None:
    with (
        pytest.raises(DBAPIError, match="permission denied"),
        scoped_session(factory, world.user_a) as session,
    ):
        session.execute(text("SELECT * FROM ingestion_runs"))


def test_deleting_a_canonical_version_nulls_duplicate_of(
    factory: sessionmaker[Session], world: World
) -> None:
    with factory.begin() as session:
        duplicate = add_document(
            session, world.source, world.user_a, duplicate_of=world.a.version
        )
    with scoped_session(factory, world.user_a) as session:
        assert delete(session, "documents", [world.a.document]) == 1
    with factory() as session:
        version = session.get(DocumentVersion, duplicate.version)
        assert version is not None
        assert version.duplicate_of is None
