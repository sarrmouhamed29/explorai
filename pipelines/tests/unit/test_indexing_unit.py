"""Indexing logic against in-memory fakes of the repository and the index."""

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pytest

from explorai_ai.chunking import ChunkText, SectionInput
from explorai_ai.config import RagSettings
from explorai_ai.errors import ProviderError, ProviderUnavailable
from explorai_ai.fakes import FakeEmbedder
from explorai_ai.index import IndexPoint, QdrantIndex
from explorai_ai.scope import PUBLIC_OWNER
from explorai_pipelines import indexing
from explorai_pipelines.flows import indexing as flows
from explorai_pipelines.flows import runtime
from explorai_pipelines.indexing import (
    INDEX_LOCK,
    PRUNE_LOCK,
    chunk_pending_versions,
    index_pending,
    prune_index,
)
from explorai_pipelines.repository import PendingChunk, VersionToChunk

DIM = 8
# The fake provider has an index name of its own (``fake_`` prefix).
A = "chunks_fake_model_a_8"
B = "chunks_fake_model_b_8"
WORDS = "dense and sparse retrieval complement each other in practice"


def rag_settings(**overrides: Any) -> RagSettings:
    values: dict[str, Any] = {
        "provider": "fake",
        "embedding_model": "model-a",
        "embedding_dimension": DIM,
        "embed_batch_size": 2,
    }
    return RagSettings(**(values | overrides))


class FakeRepo:
    """Chunks with their index entries, versions to chunk, and held lock keys."""

    def __init__(self) -> None:
        self.held: set[int] = set()
        self.to_chunk: list[VersionToChunk] = []
        self.saved: dict[uuid.UUID, list[ChunkText]] = {}
        self.chunks: list[PendingChunk] = []
        self.entries: set[tuple[uuid.UUID, str]] = set()
        self.ineligible: set[uuid.UUID] = set()
        self.record_calls = 0
        self.events: list[str] = []

    @contextmanager
    def advisory_lock(self, key: int) -> Iterator[bool]:
        if key in self.held:
            yield False
            return
        self.held.add(key)
        try:
            yield True
        finally:
            self.held.discard(key)

    def versions_to_chunk(self, limit: int) -> list[VersionToChunk]:
        pending = [v for v in self.to_chunk if v.version_id not in self.saved]
        return pending[:limit]

    def save_chunks(self, version_id: uuid.UUID, chunks: Sequence[ChunkText]) -> None:
        self.saved[version_id] = list(chunks)

    def add_chunks(self, count: int, owner: uuid.UUID | None = None) -> list[uuid.UUID]:
        document, version = uuid.uuid4(), uuid.uuid4()
        made = [
            PendingChunk(
                uuid.uuid4(), document, version, owner, "Intro", f"{WORDS} {i}"
            )
            for i in range(count)
        ]
        self.chunks.extend(made)
        return [c.chunk_id for c in made]

    def pending_chunks(self, index_name: str, limit: int) -> list[PendingChunk]:
        pending = [
            c
            for c in self.chunks
            if (c.chunk_id, index_name) not in self.entries
            and c.chunk_id not in self.ineligible
        ]
        return pending[:limit]

    def record_entries(self, chunk_ids: Sequence[uuid.UUID], index_name: str) -> None:
        self.record_calls += 1
        self.entries.update((c, index_name) for c in chunk_ids)

    def clear_entries(self, index_name: str) -> int:
        self.events.append(f"clear {index_name}")
        gone = {e for e in self.entries if e[1] == index_name}
        self.entries -= gone
        return len(gone)

    def eligible_chunk_ids(self) -> set[uuid.UUID]:
        return {c.chunk_id for c in self.chunks} - self.ineligible


class FakeIndex:
    def __init__(self) -> None:
        self.collections: dict[str, dict[uuid.UUID, IndexPoint]] = {}
        self.alias: str | None = None
        self.deletes: list[int] = []
        self.events: list[str] = []

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def ensure_collection(self, model: str, dimension: int) -> str:
        name = QdrantIndex.collection_name(model, dimension)
        self.events.append(f"ensure {name}")
        self.collections.setdefault(name, {})
        return name

    def upsert(self, collection: str, points: Sequence[IndexPoint]) -> None:
        self.events.append(f"upsert {collection}")
        for p in points:
            self.collections[collection][p.id] = p

    def delete(self, collection: str, ids: Sequence[uuid.UUID]) -> None:
        self.deletes.append(len(ids))
        for i in ids:
            self.collections[collection].pop(i, None)

    def alias_target(self) -> str | None:
        return self.alias

    def switch_alias(self, collection: str) -> None:
        self.alias = collection

    def point_ids(self, collection: str) -> Iterator[uuid.UUID]:
        yield from list(self.collections[collection])


class RecordingEmbedder(FakeEmbedder):
    """FakeEmbedder that records batch sizes and can fail from a given call on."""

    def __init__(
        self,
        fail_from_call: int | None = None,
        error: type[Exception] | None = None,
        dimension: int = DIM,
    ) -> None:
        super().__init__(dimension=dimension)
        self.calls: list[int] = []
        self._fail_from = fail_from_call
        self._error = error or ProviderUnavailable

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        if self._fail_from is not None and len(self.calls) >= self._fail_from:
            raise self._error("embedding provider down")
        return super().embed_documents(texts)


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def index() -> FakeIndex:
    return FakeIndex()


@pytest.mark.parametrize("provider", ["fake", "real"])
def test_the_collection_and_the_entries_use_the_settings_index_name(
    repo: FakeRepo, index: FakeIndex, provider: str
) -> None:
    ids = repo.add_chunks(2)
    settings = rag_settings(
        provider=provider,
        anthropic_api_key="test-not-a-key",
        voyage_api_key="test-not-a-key",
    )

    index_pending(repo, RecordingEmbedder(), index, settings)

    # One definition of the name for the vectors, the entries and the alias.
    assert set(index.collections) == {settings.index_name}
    assert index.alias == settings.index_name
    assert {(i, settings.index_name) for i in ids} == repo.entries
    if provider == "fake":
        assert settings.index_name == A


def test_provider_outage_stops_the_pass_and_keeps_chunks_pending(
    repo: FakeRepo, index: FakeIndex
) -> None:
    ids = repo.add_chunks(5)
    embedder = RecordingEmbedder(fail_from_call=2)

    counters = index_pending(repo, embedder, index, rag_settings())

    assert counters == {
        "chunked": 0,
        "embedded": 2,
        "skipped": 0,
        "embedding_failed": 1,
        "reset_entries": 0,
    }
    collection = A
    assert set(index.collections[collection]) == set(ids[:2])
    assert [c.chunk_id for c in repo.pending_chunks(A, 10)] == ids[2:]
    # Incomplete: the alias is not created on a partial collection.
    assert index.alias is None
    # The next pass, provider back, finishes and goes live.
    counters = index_pending(repo, RecordingEmbedder(), index, rag_settings())
    assert counters["embedded"] == 3
    assert set(index.collections[collection]) == set(ids)
    assert index.alias == collection


def test_a_permanent_provider_error_also_stops_cleanly(
    repo: FakeRepo, index: FakeIndex
) -> None:
    repo.add_chunks(1)
    embedder = RecordingEmbedder(fail_from_call=1, error=ProviderError)

    counters = index_pending(repo, embedder, index, rag_settings())

    assert counters["embedding_failed"] == 1
    assert counters["embedded"] == 0
    assert repo.entries == set()


def test_batches_respect_the_size(repo: FakeRepo, index: FakeIndex) -> None:
    repo.add_chunks(5)
    embedder = RecordingEmbedder()

    counters = index_pending(repo, embedder, index, rag_settings(embed_batch_size=2))

    assert embedder.calls == [2, 2, 1]
    assert counters["embedded"] == 5
    assert repo.record_calls == 3


def test_lock_held_means_skipped(repo: FakeRepo, index: FakeIndex) -> None:
    repo.add_chunks(2)
    repo.to_chunk.append(
        VersionToChunk(uuid.uuid4(), uuid.uuid4(), None, [SectionInput("H", WORDS)])
    )
    embedder = RecordingEmbedder()

    with repo.advisory_lock(INDEX_LOCK):
        counters = index_pending(repo, embedder, index, rag_settings())

    assert counters == {
        "chunked": 0,
        "embedded": 0,
        "skipped": 1,
        "embedding_failed": 0,
        "reset_entries": 0,
    }
    assert embedder.calls == []
    assert repo.saved == {}
    assert index.collections == {}


def test_points_carry_owner_document_version_and_both_vectors(
    repo: FakeRepo, index: FakeIndex
) -> None:
    user = uuid.uuid4()
    [private] = repo.add_chunks(1, owner=user)
    [public] = repo.add_chunks(1)

    index_pending(repo, RecordingEmbedder(), index, rag_settings())

    points = index.collections[A]
    assert points[private].owner == str(user)
    assert points[public].owner == PUBLIC_OWNER
    chunk = repo.chunks[0]
    assert (points[private].document_id, points[private].version_id) == (
        chunk.document_id,
        chunk.version_id,
    )
    assert len(points[private].dense) == DIM
    assert points[private].sparse.indices


def test_versions_are_chunked_before_embedding(repo: FakeRepo) -> None:
    short = VersionToChunk(
        uuid.uuid4(), uuid.uuid4(), None, [SectionInput("H", "too short")]
    )
    long = VersionToChunk(
        uuid.uuid4(),
        uuid.uuid4(),
        None,
        [SectionInput("H", WORDS), SectionInput(None, WORDS)],
    )
    repo.to_chunk.extend([short, long])

    assert chunk_pending_versions(repo, rag_settings()) == 2
    assert repo.saved[short.version_id] == []
    assert [c.ordinal for c in repo.saved[long.version_id]] == [0, 1]
    assert chunk_pending_versions(repo, rag_settings()) == 0


def test_chunking_honours_the_limit(repo: FakeRepo) -> None:
    repo.to_chunk.extend(
        VersionToChunk(uuid.uuid4(), uuid.uuid4(), None, []) for _ in range(3)
    )

    assert chunk_pending_versions(repo, rag_settings(), limit=2) == 2
    assert chunk_pending_versions(repo, rag_settings(), limit=2) == 1


def test_model_change_switches_the_alias_only_once_complete(
    repo: FakeRepo, index: FakeIndex
) -> None:
    repo.add_chunks(3)
    index_pending(repo, RecordingEmbedder(), index, rag_settings())
    assert index.alias == A

    new = rag_settings(embedding_model="model-b", embed_batch_size=1)
    index_pending(repo, RecordingEmbedder(fail_from_call=2), index, new)
    assert index.alias == A
    assert len(index.collections[B]) == 1

    index_pending(repo, RecordingEmbedder(), index, new)
    assert index.alias == B


def test_alias_left_alone_when_already_current(
    repo: FakeRepo, index: FakeIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_pending(repo, RecordingEmbedder(), index, rag_settings())
    switches: list[str] = []
    monkeypatch.setattr(index, "switch_alias", switches.append)

    index_pending(repo, RecordingEmbedder(), index, rag_settings())

    assert switches == []


def test_dimension_mismatch_is_a_configuration_error(
    repo: FakeRepo, index: FakeIndex
) -> None:
    with pytest.raises(ValueError, match="RAG_EMBEDDING_DIMENSION"):
        index_pending(repo, FakeEmbedder(dimension=DIM + 1), index, rag_settings())


def test_entries_not_written_stop_the_pass_instead_of_looping(
    repo: FakeRepo, index: FakeIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.add_chunks(1)
    monkeypatch.setattr(repo, "record_entries", lambda ids, name: None)

    with pytest.raises(RuntimeError, match="still reported as pending"):
        index_pending(repo, RecordingEmbedder(), index, rag_settings())


def test_prune_deletes_ineligible_points_of_the_alias_target_in_batches(
    repo: FakeRepo, index: FakeIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = repo.add_chunks(7)
    index_pending(repo, RecordingEmbedder(), index, rag_settings(embed_batch_size=64))
    repo.ineligible.update(ids[:5])
    # Another collection (a model change in progress) is not touched.
    other = index.ensure_collection("model-b", DIM)
    index.collections[other][ids[0]] = index.collections[A][ids[0]]
    monkeypatch.setattr(indexing, "PRUNE_BATCH", 2)

    counters = prune_index(repo, index, rag_settings())

    assert counters == {"pruned": 5, "skipped": 0}
    assert index.deletes == [2, 2, 1]
    assert set(index.collections[A]) == set(ids[5:])
    assert ids[0] in index.collections[other]


def test_prune_without_alias_does_nothing(repo: FakeRepo, index: FakeIndex) -> None:
    assert prune_index(repo, index, rag_settings()) == {"pruned": 0, "skipped": 0}
    assert index.deletes == []


def test_prune_lock_held_means_skipped(repo: FakeRepo, index: FakeIndex) -> None:
    ids = repo.add_chunks(1)
    index_pending(repo, RecordingEmbedder(), index, rag_settings())
    repo.ineligible.update(ids)

    with repo.advisory_lock(PRUNE_LOCK):
        counters = prune_index(repo, index, rag_settings())

    assert counters == {"pruned": 0, "skipped": 1}
    assert index.deletes == []


def test_index_and_prune_use_distinct_locks() -> None:
    assert len({INDEX_LOCK, PRUNE_LOCK, 4242001}) == 3


# flows


def fake_runtime(
    repo: FakeRepo, index: FakeIndex, embedder: FakeEmbedder
) -> runtime.Runtime:
    return runtime.Runtime(
        settings=None,  # type: ignore[arg-type]
        repo=repo,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        arxiv=None,  # type: ignore[arg-type]
        rag=runtime.RagRuntime(
            settings=rag_settings(),
            embedder=embedder,
            index=index,  # type: ignore[arg-type]
        ),
    )


def test_index_flow_reports_an_outage_without_failing(
    repo: FakeRepo, index: FakeIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.add_chunks(1)
    built = fake_runtime(repo, index, RecordingEmbedder(fail_from_call=1))
    monkeypatch.setattr(runtime, "build_runtime", lambda: built)

    counters = flows.index_pending_flow.fn()

    assert counters["embedding_failed"] == 1


def test_prune_flow_runs_against_the_runtime(
    repo: FakeRepo, index: FakeIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = fake_runtime(repo, index, RecordingEmbedder())
    monkeypatch.setattr(runtime, "build_runtime", lambda: built)

    assert flows.prune_index_flow.fn() == {"pruned": 0, "skipped": 0}


def test_flows_name_the_missing_variable_without_rag(
    repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = runtime.Runtime(
        settings=None,  # type: ignore[arg-type]
        repo=repo,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        arxiv=None,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(runtime, "build_runtime", lambda: built)

    with pytest.raises(runtime.RagNotConfigured, match="RAG_PROVIDER"):
        flows.index_pending_flow.fn()
    with pytest.raises(runtime.RagNotConfigured, match="RAG_PROVIDER"):
        flows.prune_index_flow.fn()


def test_rag_runtime_is_none_without_the_provider_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RAG_PROVIDER", raising=False)
    assert runtime.rag_configured() is False
    assert runtime.build_rag_runtime() is None
    monkeypatch.setenv("RAG_PROVIDER", "  ")
    assert runtime.rag_configured() is False


def test_rag_runtime_is_built_from_the_environment_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_PROVIDER", "fake")
    monkeypatch.setenv("RAG_EMBEDDING_DIMENSION", "16")
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:1")
    monkeypatch.delenv("QDRANT_API_KEY", raising=False)

    rag = runtime.build_rag_runtime()

    assert rag is not None
    assert rag.settings.embedding_dimension == 16
    assert rag.embedder.dimension == 16


def test_lost_collection_clears_its_entries_before_any_upsert(
    repo: FakeRepo, index: FakeIndex
) -> None:
    ids = repo.add_chunks(3)
    index_pending(repo, RecordingEmbedder(), index, rag_settings())
    other = {(ids[0], "chunks_another_index_8")}
    repo.entries |= other
    # Qdrant lost the collection; PostgreSQL still says every chunk is in it.
    del index.collections[A]
    index.events.clear()
    log: list[str] = []
    index.events = repo.events = log

    counters = index_pending(repo, RecordingEmbedder(), index, rag_settings())

    assert counters["reset_entries"] == 3
    assert counters["embedded"] == 3
    assert log[:2] == [f"clear {A}", f"ensure {A}"]
    assert log.index(f"clear {A}") < log.index(f"upsert {A}")
    assert set(index.collections[A]) == set(ids)
    # Entries of other indexes are untouched.
    assert other <= repo.entries


def test_existing_collection_keeps_its_entries(
    repo: FakeRepo, index: FakeIndex
) -> None:
    repo.add_chunks(2)
    index_pending(repo, RecordingEmbedder(), index, rag_settings())
    entries = set(repo.entries)
    repo.events.clear()

    counters = index_pending(repo, RecordingEmbedder(), index, rag_settings())

    assert (counters["reset_entries"], counters["embedded"]) == (0, 0)
    assert repo.events == []
    assert repo.entries == entries


def test_dimension_change_is_a_new_index_with_every_chunk_pending(
    repo: FakeRepo, index: FakeIndex
) -> None:
    ids = repo.add_chunks(3)
    index_pending(repo, RecordingEmbedder(), index, rag_settings())
    wider = rag_settings(embedding_dimension=16, embed_batch_size=1)
    new = "chunks_fake_model_a_16"

    stopped = index_pending(
        repo, RecordingEmbedder(fail_from_call=2, dimension=16), index, wider
    )

    assert stopped["embedded"] == 1
    assert [c.chunk_id for c in repo.pending_chunks(new, 10)] == ids[1:]
    assert index.alias == A
    index_pending(repo, RecordingEmbedder(dimension=16), index, wider)
    assert index.alias == new
    assert set(index.collections[new]) == set(ids)
