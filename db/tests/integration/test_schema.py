import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from explorai_db.migrate import downgrade_to_base, upgrade_to_head
from explorai_db.models import (
    Base,
    Document,
    DocumentVersion,
    RawArtifact,
    Source,
    User,
)
from explorai_db.session import make_engine, make_session_factory
from explorai_db.testing import temporary_database

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "sources",
    "raw_artifacts",
    "documents",
    "document_versions",
    "sections",
    "ingestion_runs",
    "users",
    "chunks",
    "chunk_index_entries",
}


def test_upgrade_head_creates_all_tables(database_url: str) -> None:
    tables = set(inspect(make_engine(database_url)).get_table_names())
    assert tables >= EXPECTED_TABLES


def test_models_match_migrations(database_url: str) -> None:
    with make_engine(database_url).connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []


def test_downgrade_base_drops_tables(database_url: str) -> None:
    with temporary_database(database_url, migrate=False) as url:
        upgrade_to_head(url)
        downgrade_to_base(url)
        tables = set(inspect(make_engine(url)).get_table_names())
        assert not EXPECTED_TABLES & tables


def _source(session_factory_url: str) -> uuid.UUID:
    session = make_session_factory(make_engine(session_factory_url))()
    with session.begin():
        source = Source(kind="arxiv", name=f"s-{uuid.uuid4()}", config={})
        session.add(source)
        session.flush()
        return source.id


def test_public_corpus_artifact_is_unique(database_url: str) -> None:
    source_id = _source(database_url)
    factory = make_session_factory(make_engine(database_url))

    def artifact() -> RawArtifact:
        return RawArtifact(
            source_id=source_id,
            external_id="2401.00001",
            url="https://arxiv.org/pdf/2401.00001",
            sha256="a" * 64,
            storage_key="a" * 64,
            owner_id=None,
        )

    with factory.begin() as session:
        session.add(artifact())
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(artifact())


def test_public_corpus_document_is_unique(database_url: str) -> None:
    source_id = _source(database_url)
    factory = make_session_factory(make_engine(database_url))
    with factory.begin() as session:
        session.add(Document(source_id=source_id, external_id="x", owner_id=None))
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(Document(source_id=source_id, external_id="x", owner_id=None))


def _user(session: Session, email: str | None = None) -> uuid.UUID:
    user = User(email=email or f"{uuid.uuid4().hex}@example.org", password_hash="x")
    session.add(user)
    session.flush()
    return user.id


def test_same_document_for_two_owners_is_allowed(database_url: str) -> None:
    source_id = _source(database_url)
    factory = make_session_factory(make_engine(database_url))
    with factory.begin() as session:
        for owner in (_user(session), _user(session), None):
            session.add(Document(source_id=source_id, external_id="y", owner_id=owner))


def test_owner_must_be_an_existing_user(database_url: str) -> None:
    source_id = _source(database_url)
    factory = make_session_factory(make_engine(database_url))
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(
            Document(source_id=source_id, external_id="w", owner_id=uuid.uuid4())
        )


def test_email_unique_case_insensitive(database_url: str) -> None:
    factory = make_session_factory(make_engine(database_url))
    local = uuid.uuid4().hex
    with factory.begin() as session:
        _user(session, f"{local}@Example.org")
    with pytest.raises(IntegrityError), factory.begin() as session:
        _user(session, f"{local.upper()}@EXAMPLE.ORG")


def test_upload_source_exists(database_url: str) -> None:
    factory = make_session_factory(make_engine(database_url))
    with factory() as session:
        found = session.scalars(
            select(Source).where(Source.kind == "upload", Source.name == "upload")
        ).all()
    assert len(found) == 1


def test_invalid_status_rejected(database_url: str) -> None:
    source_id = _source(database_url)
    factory = make_session_factory(make_engine(database_url))
    with factory.begin() as session:
        document = Document(source_id=source_id, external_id="z", owner_id=None)
        artifact = RawArtifact(
            source_id=source_id,
            external_id="z",
            url="https://example.org/z",
            sha256="b" * 64,
            storage_key="b" * 64,
            owner_id=None,
        )
        session.add_all([document, artifact])
        session.flush()
        document_id, artifact_id = document.id, artifact.id
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(
            DocumentVersion(
                document_id=document_id,
                artifact_id=artifact_id,
                extractor_version="pdf-1",
                status="bogus",
            )
        )
