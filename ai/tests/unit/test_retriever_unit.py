"""Retriever orchestration with in-memory stand-ins for Qdrant and PostgreSQL.

The real index and store are exercised in tests/integration/test_retriever.py;
here every stage's decision is pinned without services.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from explorai_ai import scope as scope_module
from explorai_ai.config import RagSettings
from explorai_ai.errors import (
    ProviderError,
    ProviderUnavailable,
    RetrievalUnavailable,
)
from explorai_ai.fakes import FakeEmbedder
from explorai_ai.index import QdrantIndex
from explorai_ai.models import RerankHit, ScoredId
from explorai_ai.pipeline import AnswerPipeline
from explorai_ai.retriever import Candidate, RetrievalResult, Retriever, rrf_scores
from explorai_ai.scope import build_scope_filter
from explorai_ai.sparse import SparseVector, encode_query
from explorai_ai.store import ChunkRecord, ChunkStore

USER = uuid.UUID("11111111-1111-1111-1111-111111111111")
DOCUMENT = uuid.UUID("22222222-2222-2222-2222-222222222222")
QUESTION = "How is attention computed?"
IDS = [uuid.UUID(int=n) for n in range(1, 11)]


def record(chunk_id: uuid.UUID) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=chunk_id,
        document_id=DOCUMENT,
        version_id=uuid.UUID(int=99),
        document_title="Paper",
        heading="Intro",
        text=f"text of {chunk_id.int}",
    )


@dataclass
class SearchCall:
    dense: list[float]
    sparse: SparseVector
    flt: Any
    dense_limit: int
    sparse_limit: int
    limit: int


@dataclass
class FakeIndex:
    """Answers the n-th search with the n-th scripted ranking."""

    rankings: list[list[uuid.UUID]]
    error: Exception | None = None
    calls: list[SearchCall] = field(default_factory=list)

    def search(
        self,
        *,
        dense: list[float],
        sparse: SparseVector,
        flt: Any,
        dense_limit: int,
        sparse_limit: int,
        limit: int,
    ) -> list[ScoredId]:
        self.calls.append(
            SearchCall(dense, sparse, flt, dense_limit, sparse_limit, limit)
        )
        if self.error is not None:
            raise self.error
        ranking = self.rankings[len(self.calls) - 1]
        return [ScoredId(i, 1.0 / (n + 1)) for n, i in enumerate(ranking)]


@dataclass
class FakeStore:
    """Returns the known ids only, in an order unrelated to the request."""

    known: set[uuid.UUID]
    calls: list[tuple[uuid.UUID, list[uuid.UUID]]] = field(default_factory=list)

    def hydrate(
        self, user_id: uuid.UUID, ids: Sequence[uuid.UUID]
    ) -> list[ChunkRecord]:
        self.calls.append((user_id, list(ids)))
        return [record(i) for i in sorted(ids, key=lambda i: -i.int) if i in self.known]


@dataclass
class RecordingEmbedder:
    error: Exception | None = None
    texts: list[str] = field(default_factory=list)
    model: str = "fake"
    dimension: int = 8

    def embed_query(self, text: str) -> list[float]:
        self.texts.append(text)
        if self.error is not None:
            raise self.error
        return FakeEmbedder(self.dimension).embed_query(text)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise AssertionError("the retriever embeds queries only")


@dataclass
class ScriptedReranker:
    hits: list[RerankHit] | None = None  # None: score by position, best first
    error: Exception | None = None
    calls: list[tuple[str, list[str], int]] = field(default_factory=list)

    def rerank(
        self, query: str, documents: Sequence[str], top_k: int
    ) -> list[RerankHit]:
        self.calls.append((query, list(documents), top_k))
        if self.error is not None:
            raise self.error
        if self.hits is not None:
            return self.hits[:top_k]
        # Reverse the order it was given, to make reranking visible.
        count = len(documents)
        return [RerankHit(i, (i + 1) / count) for i in reversed(range(count))][:top_k]


@dataclass
class ScriptedGenerator:
    rewrites: list[str] | Exception = field(default_factory=list)
    calls: list[tuple[str, int]] = field(default_factory=list)

    def rewrite_queries(self, question: str, max_queries: int) -> list[str]:
        self.calls.append((question, max_queries))
        if isinstance(self.rewrites, Exception):
            raise self.rewrites
        return self.rewrites

    def answer(self, question: str, sources: Sequence[Any]) -> Any:
        raise AssertionError("the retriever never answers")


def settings(**overrides: object) -> RagSettings:
    values: dict[str, object] = {"provider": "fake", **overrides}
    return RagSettings(**values)  # type: ignore[arg-type]


@dataclass
class Rig:
    index: FakeIndex
    store: FakeStore
    embedder: RecordingEmbedder
    reranker: ScriptedReranker
    generator: ScriptedGenerator
    retriever: Retriever


def rig(
    rankings: list[list[uuid.UUID]],
    *,
    known: Sequence[uuid.UUID] = IDS,
    index_error: Exception | None = None,
    embed_error: Exception | None = None,
    reranker: ScriptedReranker | None = None,
    generator: ScriptedGenerator | None = None,
    **overrides: object,
) -> Rig:
    index = FakeIndex(rankings, index_error)
    store = FakeStore(set(known))
    embedder = RecordingEmbedder(embed_error)
    reranker = reranker or ScriptedReranker()
    generator = generator or ScriptedGenerator()
    retriever = Retriever(
        index=cast(QdrantIndex, index),
        embedder=embedder,
        reranker=reranker,
        generator=generator,
        store=cast(ChunkStore, store),
        settings=settings(**overrides),
    )
    return Rig(index, store, embedder, reranker, generator, retriever)


def ids(result: RetrievalResult) -> list[uuid.UUID]:
    return [c.chunk_id for c in result.candidates]


def test_queries_are_the_question_plus_distinct_rewrites() -> None:
    generator = ScriptedGenerator(
        [
            "how is ATTENTION computed?",
            "attention formula",
            "",
            "  ",
            "Attention Formula",
            "softmax of scaled dot products",
            "query key value",
            "one too many",
        ]
    )
    r = rig([[]] * 4, generator=generator)
    r.retriever.retrieve(USER, QUESTION)
    assert generator.calls == [(QUESTION, 3)]
    # The question first, case-insensitive duplicates and blanks dropped, 4 at most.
    assert r.embedder.texts == [
        QUESTION,
        "attention formula",
        "softmax of scaled dot products",
        "query key value",
    ]
    assert [c.sparse for c in r.index.calls] == [
        encode_query(t) for t in r.embedder.texts
    ]


def test_rewriting_disabled_searches_the_question_only() -> None:
    generator = ScriptedGenerator(["other query"])
    r = rig([[]], generator=generator, rewrite_enabled=False)
    r.retriever.retrieve(USER, QUESTION)
    assert generator.calls == []
    assert r.embedder.texts == [QUESTION]


@pytest.mark.parametrize(
    "failure", [ProviderUnavailable("timeout"), ProviderError("bad reply")]
)
def test_rewrite_failure_searches_the_question_alone(failure: ProviderError) -> None:
    r = rig([[IDS[0]]], generator=ScriptedGenerator(failure))
    result = r.retriever.retrieve(USER, QUESTION)
    assert r.embedder.texts == [QUESTION]
    assert ids(result) == [IDS[0]]


@pytest.mark.parametrize("scope", ["all", "mine", "public"])
@pytest.mark.parametrize("document_id", [None, DOCUMENT])
def test_every_search_carries_the_scope_filter(
    scope: scope_module.Scope, document_id: uuid.UUID | None
) -> None:
    r = rig([[], []], generator=ScriptedGenerator(["second query"]))
    r.retriever.retrieve(USER, QUESTION, scope=scope, document_id=document_id)
    expected = build_scope_filter(USER, scope, document_id)
    assert [c.flt for c in r.index.calls] == [expected, expected]
    defaults = settings()
    for call in r.index.calls:
        assert (call.dense_limit, call.sparse_limit, call.limit) == (
            defaults.dense_candidates,
            defaults.sparse_candidates,
            defaults.fused_candidates,
        )


def test_the_filter_comes_from_the_scope_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The isolation test of Task 9 replaces this function to prove that RLS alone
    # still stops foreign chunks; that only works if it is looked up at call time.
    sentinel = object()
    seen: list[tuple[object, ...]] = []

    def fake_filter(*args: object) -> object:
        seen.append(args)
        return sentinel

    monkeypatch.setattr(scope_module, "build_scope_filter", fake_filter)
    r = rig([[]], rewrite_enabled=False)
    r.retriever.retrieve(USER, QUESTION, "public", DOCUMENT)
    assert seen == [(USER, "public", DOCUMENT)]
    assert r.index.calls[0].flt is sentinel


@pytest.mark.parametrize(
    "failure", [ProviderUnavailable("timeout"), ProviderError("bad vector")]
)
def test_query_embedding_failure_is_retrieval_unavailable(
    failure: ProviderError,
) -> None:
    r = rig([[IDS[0]]], embed_error=failure)
    with pytest.raises(RetrievalUnavailable) as raised:
        r.retriever.retrieve(USER, QUESTION)
    assert raised.value.__cause__ is failure
    assert r.index.calls == []


def test_index_outage_propagates() -> None:
    r = rig([[]], index_error=RetrievalUnavailable("vector store unreachable"))
    with pytest.raises(RetrievalUnavailable):
        r.retriever.retrieve(USER, QUESTION)


def test_pipeline_surfaces_an_index_outage_unchanged() -> None:
    # A rejected or failed vector-store request (QdrantIndex maps it to
    # RetrievalUnavailable) reaches the route as is, so it answers 503, not 500.
    outage = RetrievalUnavailable("vector store not ready")
    r = rig([[]], index_error=outage, rewrite_enabled=False)
    pipeline = AnswerPipeline(r.retriever, r.generator, settings())
    with pytest.raises(RetrievalUnavailable) as raised:
        pipeline.ask(USER, QUESTION)
    assert raised.value is outage


def test_empty_search_is_an_empty_result() -> None:
    r = rig([[], []], generator=ScriptedGenerator(["second"]))
    assert r.retriever.retrieve(USER, QUESTION) == RetrievalResult([], None)
    assert r.store.calls == []
    assert r.reranker.calls == []


def test_queries_are_fused_then_cut_to_fused_candidates() -> None:
    first = [IDS[0], IDS[1], IDS[2], IDS[3]]
    second = [IDS[1], IDS[2], IDS[4]]
    r = rig(
        [first, second],
        generator=ScriptedGenerator(["second"]),
        fused_candidates=3,
        rerank_enabled=False,
    )
    result = r.retriever.retrieve(USER, QUESTION)
    # IDS[1] and IDS[2] are in both lists; IDS[0] beats IDS[4] (first vs third).
    expected = [IDS[1], IDS[2], IDS[0]]
    assert r.store.calls == [(USER, expected)]
    assert ids(result) == expected
    scores = rrf_scores([first, second])
    assert [c.score for c in result.candidates] == [scores[i] for i in expected]
    assert result.best_score is None


def test_hydration_drops_missing_ids_and_keeps_the_fused_order() -> None:
    r = rig([IDS[:5]], known=[IDS[4], IDS[1], IDS[3]], rerank_enabled=False)
    result = r.retriever.retrieve(USER, QUESTION)
    assert ids(result) == [IDS[1], IDS[3], IDS[4]]
    first = result.candidates[0]
    assert first == Candidate(
        chunk_id=IDS[1],
        document_id=DOCUMENT,
        version_id=uuid.UUID(int=99),
        document_title="Paper",
        heading="Intro",
        text=f"text of {IDS[1].int}",
        score=first.score,
    )


def test_nothing_visible_is_an_empty_result_without_reranking() -> None:
    r = rig([IDS[:3]], known=[])
    assert r.retriever.retrieve(USER, QUESTION) == RetrievalResult([], None)
    assert r.reranker.calls == []


def test_rerank_reorders_keeps_final_candidates_and_reports_the_best_score() -> None:
    r = rig([IDS[:5]], final_candidates=2)
    result = r.retriever.retrieve(USER, QUESTION)
    [(query, documents, top_k)] = r.reranker.calls
    assert query == QUESTION  # the user's question, not a rewrite
    assert documents == [f"text of {i.int}" for i in IDS[:5]]
    assert top_k == 2
    assert ids(result) == [IDS[4], IDS[3]]
    assert [c.score for c in result.candidates] == [1.0, 0.8]
    assert result.best_score == 1.0


def test_rerank_hits_outside_the_candidates_are_ignored() -> None:
    reranker = ScriptedReranker(
        hits=[
            RerankHit(7, 0.99),
            RerankHit(1, 0.5),
            RerankHit(1, 0.4),
            RerankHit(0, 0.3),
        ]
    )
    r = rig([IDS[:2]], reranker=reranker)
    result = r.retriever.retrieve(USER, QUESTION)
    assert ids(result) == [IDS[1], IDS[0]]
    assert result.best_score == 0.5


@pytest.mark.parametrize(
    "reranker",
    [
        ScriptedReranker(error=ProviderUnavailable("timeout")),
        ScriptedReranker(error=ProviderError("malformed")),
        ScriptedReranker(hits=[]),
        ScriptedReranker(hits=[RerankHit(9, 0.9)]),
    ],
)
def test_rerank_failure_keeps_the_fused_order(reranker: ScriptedReranker) -> None:
    r = rig([IDS[:5]], reranker=reranker, final_candidates=3)
    result = r.retriever.retrieve(USER, QUESTION)
    assert ids(result) == IDS[:3]
    # No relevance score: the refusal threshold cannot be applied.
    assert result.best_score is None


def test_rerank_disabled_keeps_the_fused_order() -> None:
    r = rig([IDS[:5]], rerank_enabled=False, final_candidates=2)
    result = r.retriever.retrieve(USER, QUESTION)
    assert r.reranker.calls == []
    assert ids(result) == IDS[:2]
    assert result.best_score is None


def test_nothing_of_the_question_or_texts_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    secret = "zebra-quartz-question"
    rig(
        [IDS[:3]],
        generator=ScriptedGenerator(ProviderError("down")),
        reranker=ScriptedReranker(error=ProviderError("down")),
    ).retriever.retrieve(USER, secret)
    assert caplog.records
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert secret not in logged
    assert "text of" not in logged
