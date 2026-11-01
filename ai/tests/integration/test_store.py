"""ChunkStore: re-reads chunk text under Row-Level Security, drops stale ids.

Rows are inserted as the table owner (who bypasses RLS); the store only reads
through ``ScopedSessions.scoped``, as the API will.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from explorai_ai.store import ChunkRecord, ChunkStore
from explorai_db.models import (
    Chunk,
    ChunkIndexEntry,
    Document,
    DocumentVersion,
    RawArtifact,
    Source,
    User,
)
from explorai_db.rls import ScopedSessions
from explorai_db.session import make_engine, make_session_factory

pytestmark = pytest.mark.integration

MODEL = "embed-v1"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    engine = make_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE users, documents, raw_artifacts, document_versions, "
                "sections, chunks, chunk_index_entries CASCADE"
            )
        )
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture
def store(factory: sessionmaker[Session]) -> ChunkStore:
    return ChunkStore(ScopedSessions(factory))


class Corpus:
    """Inserts fixtures as the table owner, one short transaction each."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory
        self._owners: dict[uuid.UUID, uuid.UUID | None] = {}
        with factory.begin() as session:
            source = Source(kind="test", name=f"store-{uuid.uuid4()}", config={})
            session.add(source)
            session.flush()
            self._source = source.id

    def user(self) -> uuid.UUID:
        with self._factory.begin() as session:
            user = User(email=f"{uuid.uuid4().hex}@example.org", password_hash="x")
            session.add(user)
            session.flush()
            return user.id

    def document(self, owner: uuid.UUID | None, title: str = "A title") -> uuid.UUID:
        with self._factory.begin() as session:
            document = Document(
                source_id=self._source,
                external_id=uuid.uuid4().hex,
                owner_id=owner,
                title=title,
            )
            session.add(document)
            session.flush()
            self._owners[document.id] = owner
            return document.id

    def version(
        self,
        document_id: uuid.UUID,
        created_at: datetime,
        *,
        status: str = "ok",
        duplicate_of: uuid.UUID | None = None,
        chunked: bool = True,
        version_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        key = uuid.uuid4().hex
        with self._factory.begin() as session:
            artifact = RawArtifact(
                source_id=self._source,
                external_id=key,
                url=f"https://example.org/{key}",
                sha256=key.ljust(64, "0"),
                storage_key=key.ljust(64, "0"),
                owner_id=self._owners[document_id],
            )
            session.add(artifact)
            session.flush()
            version = DocumentVersion(
                id=version_id or uuid.uuid4(),
                document_id=document_id,
                artifact_id=artifact.id,
                extractor_version="pdf-1",
                status=status,
                duplicate_of=duplicate_of,
                created_at=created_at,
                chunked_at=created_at if chunked else None,
            )
            session.add(version)
            session.flush()
            return version.id

    def chunk(
        self,
        version_id: uuid.UUID,
        ordinal: int,
        *,
        models: tuple[str, ...] = (),
        heading: str | None = "Intro",
    ) -> uuid.UUID:
        with self._factory.begin() as session:
            chunk = Chunk(
                version_id=version_id,
                ordinal=ordinal,
                heading=heading,
                text=f"text {ordinal} of {version_id}",
                word_count=4,
                text_hash="f" * 64,
            )
            session.add(chunk)
            session.flush()
            session.add_all(
                ChunkIndexEntry(chunk_id=chunk.id, embedding_model=model)
                for model in models
            )
            return chunk.id

    def record(self, chunk_id: uuid.UUID) -> ChunkRecord:
        """What hydrate should return for ``chunk_id``, read as the owner."""
        with self._factory() as session:
            chunk = session.get(Chunk, chunk_id)
            assert chunk is not None
            version = session.get(DocumentVersion, chunk.version_id)
            assert version is not None
            document = session.get(Document, version.document_id)
            assert document is not None
            return ChunkRecord(
                chunk_id=chunk.id,
                document_id=document.id,
                version_id=version.id,
                document_title=document.title,
                heading=chunk.heading,
                text=chunk.text,
            )


@pytest.fixture
def corpus(factory: sessionmaker[Session]) -> Corpus:
    return Corpus(factory)


def test_hydrate_returns_visible_latest_canonical_chunks_only(
    store: ChunkStore, corpus: Corpus
) -> None:
    user_a, user_b = corpus.user(), corpus.user()

    # A's document: an older version, the latest ok one, then a failed attempt
    # (a failed version never replaces the latest ok one).
    paper = corpus.document(user_a, title="Paper")
    old = corpus.version(paper, at(0))
    current = corpus.version(paper, at(1))
    corpus.version(paper, at(2), status="failed", chunked=False)
    old_chunk = corpus.chunk(old, 0)
    first = corpus.chunk(current, 0)
    second = corpus.chunk(current, 1, heading=None)

    # A's copy: its latest ok version is a duplicate, so none of its versions
    # is searchable, not even the older canonical one.
    copy = corpus.document(user_a)
    copy_old = corpus.version(copy, at(0))
    copy_dup = corpus.version(copy, at(1), duplicate_of=current)
    copy_old_chunk = corpus.chunk(copy_old, 0)
    copy_dup_chunk = corpus.chunk(copy_dup, 0)

    # B's document, invisible to A.
    other = corpus.document(user_b)
    other_chunk = corpus.chunk(corpus.version(other, at(0)), 0)

    requested = [
        second,
        old_chunk,
        other_chunk,
        copy_old_chunk,
        copy_dup_chunk,
        uuid.uuid4(),  # unknown
        first,
        second,  # repeated
    ]
    assert store.hydrate(user_a, requested) == [
        corpus.record(second),
        corpus.record(first),
    ]
    # B sees its own chunk and nothing of A's.
    assert store.hydrate(user_b, requested) == [corpus.record(other_chunk)]


def test_hydrate_record_fields(store: ChunkStore, corpus: Corpus) -> None:
    user = corpus.user()
    document = corpus.document(user, title="Attention")
    version = corpus.version(document, at(0))
    chunk = corpus.chunk(version, 3, heading="Method")
    [record] = store.hydrate(user, [chunk])
    assert record == ChunkRecord(
        chunk_id=chunk,
        document_id=document,
        version_id=version,
        document_title="Attention",
        heading="Method",
        text=f"text 3 of {version}",
    )


def test_hydrate_keeps_public_chunks_for_everyone(
    store: ChunkStore, corpus: Corpus
) -> None:
    public = corpus.document(None)
    chunk = corpus.chunk(corpus.version(public, at(0)), 0)
    expected = [corpus.record(chunk)]
    # Two accounts, and an id with no account row at all.
    for user in (corpus.user(), corpus.user(), uuid.uuid4()):
        assert store.hydrate(user, [chunk]) == expected


def test_latest_version_tie_is_broken_by_id(store: ChunkStore, corpus: Corpus) -> None:
    user = corpus.user()
    document = corpus.document(user)
    low, high = sorted([uuid.uuid4(), uuid.uuid4()])
    corpus.version(document, at(0), version_id=high)
    corpus.version(document, at(0), version_id=low)
    low_chunk = corpus.chunk(low, 0)
    high_chunk = corpus.chunk(high, 0)
    assert store.hydrate(user, [low_chunk, high_chunk]) == [corpus.record(high_chunk)]


def test_hydrate_without_ids_returns_nothing(store: ChunkStore) -> None:
    assert store.hydrate(uuid.uuid4(), []) == []


def test_indexed_state_truth_table(store: ChunkStore, corpus: Corpus) -> None:
    user, other_user = corpus.user(), corpus.user()

    no_version = corpus.document(user)

    only_failed = corpus.document(user)
    corpus.version(only_failed, at(0), status="failed", chunked=False)

    not_chunked = corpus.document(user)
    corpus.chunk(corpus.version(not_chunked, at(0), chunked=False), 0)

    no_entries = corpus.document(user)
    no_entries_version = corpus.version(no_entries, at(0))
    corpus.chunk(no_entries_version, 0)
    corpus.chunk(no_entries_version, 1)

    other_model = corpus.document(user)
    corpus.chunk(corpus.version(other_model, at(0)), 0, models=("embed-v0",))

    partial = corpus.document(user)
    partial_version = corpus.version(partial, at(0))
    corpus.chunk(partial_version, 0, models=(MODEL,))
    corpus.chunk(partial_version, 1)

    complete = corpus.document(user)
    complete_version = corpus.version(complete, at(0))
    corpus.chunk(complete_version, 0, models=(MODEL, "embed-v0"))
    corpus.chunk(complete_version, 1, models=(MODEL,))

    grows = corpus.document(user)
    grows_version = corpus.version(grows, at(0))
    corpus.chunk(grows_version, 0, models=(MODEL,))

    zero_chunks = corpus.document(user)
    corpus.version(zero_chunks, at(0))

    # Only the latest ok version counts: an indexed older one does not help.
    superseded = corpus.document(user)
    corpus.chunk(corpus.version(superseded, at(0)), 0, models=(MODEL,))
    corpus.version(superseded, at(1), chunked=False)

    # Duplicates follow their canonical version (they have no chunks of their own).
    copy_of_complete = corpus.document(user)
    corpus.version(
        copy_of_complete, at(1), duplicate_of=complete_version, chunked=False
    )
    copy_of_pending = corpus.document(user)
    corpus.version(copy_of_pending, at(1), duplicate_of=no_entries_version)

    # A canonical version that its own document has since replaced is not in the
    # index any more (prune-index removes it), so its duplicate is not indexed.
    replaced = corpus.document(user)
    replaced_old = corpus.version(replaced, at(0))
    corpus.chunk(replaced_old, 0, models=(MODEL,))
    corpus.version(replaced, at(1), chunked=False)
    copy_of_replaced = corpus.document(user)
    corpus.version(copy_of_replaced, at(2), duplicate_of=replaced_old)

    public = corpus.document(None)
    corpus.chunk(corpus.version(public, at(0)), 0, models=(MODEL,))

    foreign = corpus.document(other_user)
    corpus.chunk(corpus.version(foreign, at(0)), 0, models=(MODEL,))

    expected = {
        no_version: False,
        only_failed: False,
        not_chunked: False,
        no_entries: False,
        other_model: False,
        partial: False,
        complete: True,
        grows: True,
        zero_chunks: True,
        superseded: False,
        copy_of_complete: True,
        copy_of_pending: False,
        replaced: False,
        copy_of_replaced: False,
        public: True,
    }
    requested = [*expected, foreign, uuid.uuid4()]  # invisible, unknown: absent
    assert store.indexed_state(user, requested, MODEL) == expected

    # A chunk added to an indexed version makes it pending again.
    corpus.chunk(grows_version, 1)
    assert store.indexed_state(user, [grows], MODEL) == {grows: False}
    # The same documents against another model.
    assert store.indexed_state(user, [complete, other_model], "embed-v0") == {
        complete: False,
        other_model: True,
    }


def test_indexed_state_without_documents_returns_nothing(store: ChunkStore) -> None:
    assert store.indexed_state(uuid.uuid4(), [], MODEL) == {}


def test_indexed_state_issues_one_query_whatever_the_count(
    engine: Engine, store: ChunkStore, corpus: Corpus
) -> None:
    user = corpus.user()
    documents = [corpus.document(user) for _ in range(5)]
    for document in documents:
        corpus.chunk(corpus.version(document, at(0)), 0, models=(MODEL,))

    statements: list[str] = []

    def record(*args: Any) -> None:
        statement = args[2]
        # Leave out the preamble of scoped_session (role switch, identity).
        if "SET LOCAL ROLE" not in statement and "set_config" not in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        store.indexed_state(user, documents[:1], MODEL)
        one = len(statements)
        statements.clear()
        assert len(store.indexed_state(user, documents, MODEL)) == 5
        five = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert one == five == 1
