"""The answer pipeline: retrieve, decide to refuse or generate, verify citations.

Three refusal levels (spec section 7), each an explicit ``insufficient_evidence``
result rather than an error:

1. before generation: no candidate at all, or the best rerank score is below
   ``refusal_threshold`` (the generator is not called: no cost, no chance to
   hallucinate from weak sources);
2. the model itself answers ``insufficient``;
3. the model answers but no citation survives verification against the chunks.

Errors: every provider failure that escapes :meth:`AnswerPipeline.ask` is either
``RetrievalUnavailable`` (from retrieval) or ``ProviderUnavailable``: transient
generation errors propagate unchanged, any other ``ProviderError`` from generation
is wrapped in ``GenerationFailed`` (a ``ProviderUnavailable``). The route maps
exactly those two classes to 503. Other exceptions are bugs and are not disguised.
"""

import logging
import time
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from explorai_ai.citations import strip_unverified_markers, verify_citations
from explorai_ai.config import RagSettings
from explorai_ai.errors import GenerationFailed, ProviderError, ProviderUnavailable
from explorai_ai.models import AnswerResult, PromptSource, Usage
from explorai_ai.providers.base import Generator
from explorai_ai.retriever import RetrievalResult
from explorai_ai.scope import Scope

NO_USAGE = Usage(input_tokens=0, output_tokens=0)
logger = logging.getLogger(__name__)


class Retrieves(Protocol):
    """What the pipeline needs from ``Retriever`` (a fake stands in for tests)."""

    def retrieve(
        self,
        user_id: UUID,
        question: str,
        scope: Scope = "all",
        document_id: UUID | None = None,
    ) -> RetrievalResult: ...


@dataclass(frozen=True)
class _Trace:
    """What the log line says about one ask: codes and counts, never text."""

    reason: str
    sources: int = 0
    raw_citations: int = 0


def _refusal(usage: Usage) -> AnswerResult:
    return AnswerResult("insufficient_evidence", None, [], usage)


class AnswerPipeline:
    def __init__(
        self, retriever: Retrieves, generator: Generator, settings: RagSettings
    ) -> None:
        self._retriever = retriever
        self._generator = generator
        self._settings = settings

    def ask(
        self,
        user_id: UUID,
        question: str,
        scope: Scope = "all",
        document_id: UUID | None = None,
    ) -> AnswerResult:
        started = time.perf_counter()
        result, trace = self._ask(user_id, question, scope, document_id)
        logger.info(
            "ask done: status=%s reason=%s sources=%d citations=%d/%d "
            "input_tokens=%d output_tokens=%d duration_ms=%d",
            result.status,
            trace.reason,
            trace.sources,
            len(result.citations),
            trace.raw_citations,
            result.usage.input_tokens,
            result.usage.output_tokens,
            (time.perf_counter() - started) * 1000,
        )
        return result

    def _ask(
        self,
        user_id: UUID,
        question: str,
        scope: Scope,
        document_id: UUID | None,
    ) -> tuple[AnswerResult, _Trace]:
        settings = self._settings
        retrieved = self._retriever.retrieve(user_id, question, scope, document_id)
        if not retrieved.candidates:
            return _refusal(NO_USAGE), _Trace("no_candidates")
        # best_score is None when reranking is off or failed: no relevance score,
        # so the threshold does not apply and the generator decides (level 2).
        if (
            settings.rerank_enabled
            and retrieved.best_score is not None
            and retrieved.best_score < settings.refusal_threshold
        ):
            return _refusal(NO_USAGE), _Trace("below_threshold")

        # Defence in depth: never more than final_candidates sources, whatever the
        # retriever returned. The prompt module neutralises tags and caps lengths.
        candidates = retrieved.candidates[: settings.final_candidates]
        sources = [
            PromptSource(
                number=number,
                title=candidate.document_title,
                heading=candidate.heading,
                text=candidate.text,
            )
            for number, candidate in enumerate(candidates, start=1)
        ]
        try:
            generated = self._generator.answer(question, sources)
        except ProviderUnavailable:
            raise
        except ProviderError as exc:
            raise GenerationFailed(f"generation failed: {type(exc).__name__}") from exc

        raw = len(generated.citations)
        if generated.status == "insufficient":
            trace = _Trace("model_insufficient", len(sources), raw)
            return _refusal(generated.usage), trace
        citations = verify_citations(generated.citations, candidates)
        if not citations:
            trace = _Trace("no_verified_citation", len(sources), raw)
            return _refusal(generated.usage), trace
        # Markers of sources whose citations were all dropped would point at
        # evidence the client never gets: remove them from the text.
        text = strip_unverified_markers(
            generated.answer, {citation.source for citation in citations}
        )
        answer = AnswerResult("answered", text, citations, generated.usage)
        return answer, _Trace("answered", len(sources), raw)
