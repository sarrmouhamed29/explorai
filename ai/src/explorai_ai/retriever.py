"""Retrieval: from a question to the few chunks the answer may be built on.

Stages (spec section 6), each degrading gracefully:

1. query rewriting (optional): a failure searches the question alone;
2. hybrid search per query in Qdrant (dense + sparse fused by RRF inside Qdrant),
   always under the scope filter, then a second RRF across the queries;
3. re-read under Row-Level Security: Qdrant only gives ids, texts come from
   PostgreSQL through ``ChunkStore``, which drops invisible and stale chunks;
4. reranking (optional): a failure keeps the fused order.

What cannot degrade: without a query embedding or a reachable Qdrant there is
nothing to search, so ``RetrievalUnavailable`` is raised (the API answers 503).
No question or chunk text is ever logged: ids, counts and durations only.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from uuid import UUID

# The module, not the function: ``scope.build_scope_filter`` is looked up at each
# call, so a test can replace it (the Task 9 isolation test swaps in a filter that
# matches everything to prove that RLS alone still stops foreign chunks). Aliased
# because ``scope`` is also the name of the retrieve() parameter.
from explorai_ai import scope as scoping
from explorai_ai.config import RagSettings
from explorai_ai.errors import ProviderError, RetrievalUnavailable
from explorai_ai.index import QdrantIndex
from explorai_ai.models import RerankHit
from explorai_ai.providers.base import Embedder, Generator, Reranker
from explorai_ai.scope import Scope
from explorai_ai.sparse import encode_query
from explorai_ai.store import ChunkRecord, ChunkStore

MAX_REWRITES = 3
MAX_QUERIES = 1 + MAX_REWRITES  # the question itself is always searched
RRF_K = 60
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    document_title: str | None
    heading: str | None
    text: str
    score: float
    """The rerank score when reranked, otherwise the fused RRF score."""


@dataclass(frozen=True)
class RetrievalResult:
    candidates: list[Candidate]
    best_score: float | None
    """Top rerank score; ``None`` when there was no successful reranking.

    ``None`` means the refusal threshold is not applicable: RRF scores are not
    relevance scores (they only depend on ranks), so they cannot be compared to
    it. With reranking disabled or failed, the generator decides alone whether
    the sources are sufficient.
    """


def rrf_scores(
    rankings: Sequence[Sequence[UUID]], *, k: int = RRF_K
) -> dict[UUID, float]:
    """Reciprocal Rank Fusion: an id scores ``sum(1 / (k + rank))`` over the rankings.

    ``rank`` is 1-based. An id repeated inside one ranking counts at its first rank
    only. ``k`` damps the weight of the very first places (60 is the usual value).
    """
    if k <= 0:
        raise ValueError("k must be positive")
    scores: dict[UUID, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(dict.fromkeys(ranking), start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def rrf_fuse(rankings: Sequence[Sequence[UUID]], *, k: int = RRF_K) -> list[UUID]:
    """The ids of all rankings, best fused score first.

    Equal scores are ordered by the id's string form, so the result does not depend
    on the order of the rankings or on dictionary order: it is deterministic.
    """
    scores = rrf_scores(rankings, k=k)
    return sorted(scores, key=lambda item: (-scores[item], str(item)))


def _distinct_queries(question: str, rewrites: Sequence[str]) -> list[str]:
    """The question first, then non-blank rewrites unseen case-insensitively."""
    queries = [question]
    seen = {question.strip().casefold()}
    for rewrite in rewrites:
        key = rewrite.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        queries.append(rewrite.strip())
        if len(queries) == MAX_QUERIES:
            break
    return queries


def _candidate(record: ChunkRecord, score: float) -> Candidate:
    return Candidate(
        chunk_id=record.chunk_id,
        document_id=record.document_id,
        version_id=record.version_id,
        document_title=record.document_title,
        heading=record.heading,
        text=record.text,
        score=score,
    )


class Retriever:
    def __init__(
        self,
        *,
        index: QdrantIndex,
        embedder: Embedder,
        reranker: Reranker,
        generator: Generator,
        store: ChunkStore,
        settings: RagSettings,
    ) -> None:
        self._index = index
        self._embedder = embedder
        self._reranker = reranker
        self._generator = generator
        self._store = store
        self._settings = settings

    def retrieve(
        self,
        user_id: UUID,
        question: str,
        scope: Scope = "all",
        document_id: UUID | None = None,
    ) -> RetrievalResult:
        """The chunks to answer from, best first, at most ``final_candidates``.

        Raises ``RetrievalUnavailable`` when the query cannot be embedded or Qdrant
        is unreachable. An empty corpus (or nothing visible) is not an error: it
        is ``RetrievalResult([], None)``, which the pipeline turns into a refusal.
        """
        started = time.perf_counter()
        settings = self._settings
        queries = self._queries(question)
        flt = scoping.build_scope_filter(user_id, scope, document_id)
        rankings: list[list[UUID]] = []
        for query in queries:
            try:
                dense = self._embedder.embed_query(query)
            except ProviderError as exc:
                raise RetrievalUnavailable("query embedding failed") from exc
            hits = self._index.search(
                dense=dense,
                sparse=encode_query(query),
                flt=flt,
                dense_limit=settings.dense_candidates,
                sparse_limit=settings.sparse_candidates,
                limit=settings.fused_candidates,
            )
            rankings.append([hit.id for hit in hits])
        scores = rrf_scores(rankings)
        fused = rrf_fuse(rankings)[: settings.fused_candidates]
        if not fused:
            logger.info("retrieval found nothing: queries=%d", len(queries))
            return RetrievalResult([], None)

        # Text comes from PostgreSQL under RLS, never from Qdrant. Reorder by
        # fused rank ourselves rather than rely on the store's ordering.
        records = {r.chunk_id: r for r in self._store.hydrate(user_id, fused)}
        hydrated = [
            _candidate(records[chunk_id], scores[chunk_id])
            for chunk_id in fused
            if chunk_id in records
        ]
        result = self._rerank(question, hydrated)
        logger.info(
            "retrieval done: queries=%d fused=%d hydrated=%d kept=%d "
            "reranked=%s duration_ms=%d",
            len(queries),
            len(fused),
            len(hydrated),
            len(result.candidates),
            result.best_score is not None,
            (time.perf_counter() - started) * 1000,
        )
        return result

    def _queries(self, question: str) -> list[str]:
        if not self._settings.rewrite_enabled:
            return [question]
        try:
            rewrites = self._generator.rewrite_queries(question, MAX_REWRITES)
        except ProviderError as exc:
            # Degrade: the original question alone. The error type only, no text.
            logger.warning(
                "query rewriting failed (%s): searching the question alone",
                type(exc).__name__,
            )
            return [question]
        return _distinct_queries(question, rewrites)

    def _rerank(self, question: str, hydrated: list[Candidate]) -> RetrievalResult:
        settings = self._settings
        fallback = RetrievalResult(hydrated[: settings.final_candidates], None)
        if not hydrated or not settings.rerank_enabled:
            return fallback
        try:
            hits = self._reranker.rerank(
                question, [c.text for c in hydrated], settings.final_candidates
            )
        except ProviderError as exc:
            # Degrade: the fused order, and no score to apply the threshold to.
            logger.warning(
                "reranking failed (%s): keeping the fused order", type(exc).__name__
            )
            return fallback
        kept = self._valid_hits(hits, len(hydrated))
        if not kept:
            logger.warning("reranking returned no usable hit: keeping the fused order")
            return fallback
        candidates = [replace(hydrated[hit.index], score=hit.score) for hit in kept]
        return RetrievalResult(candidates, candidates[0].score)

    def _valid_hits(self, hits: Sequence[RerankHit], count: int) -> list[RerankHit]:
        """In-range, first-seen hits, best score first, at most final_candidates."""
        seen: set[int] = set()
        valid: list[RerankHit] = []
        for hit in hits:
            if 0 <= hit.index < count and hit.index not in seen:
                seen.add(hit.index)
                valid.append(hit)
        valid.sort(key=lambda hit: -hit.score)  # stable: ties keep the given order
        return valid[: self._settings.final_candidates]
