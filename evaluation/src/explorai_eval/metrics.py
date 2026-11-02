"""Metrics for RAG evaluation."""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from explorai_ai.citations import normalise_for_match
from explorai_eval.config import EvalSettings
from explorai_eval.runner import EvalSample

REFUSED = "insufficient_evidence"
ANSWERED = "answered"


@dataclass(frozen=True)
class ExistingMetrics:
    retrieval_hit_rate: float | None
    correct_refusal_rate: float | None
    false_refusal_rate: float | None
    retrieval_hits: int
    n_answerable: int
    correct_refusals: int
    n_unanswerable: int
    false_refusals: int
    missed_questions: list[str] = field(default_factory=list)


def _rate(ok: int, total: int) -> float | None:
    return ok / total if total else None


def _hit(contexts: list[str], expected_quote: str) -> bool:
    needle = normalise_for_match(expected_quote)
    return any(needle in normalise_for_match(text) for text in contexts)


def compute_existing_metrics(samples: Sequence[EvalSample]) -> ExistingMetrics:
    """Port of ai/measure.py score_retrieval + score_refusal."""
    hits = correct_refusals = false_refusals = n_answerable = n_unanswerable = 0
    missed: list[str] = []

    for s in samples:
        if s.expected_quote is None:
            n_unanswerable += 1
            if s.status == REFUSED:
                correct_refusals += 1
        else:
            n_answerable += 1
            if _hit(s.retrieved_contexts, s.expected_quote):
                hits += 1
            else:
                missed.append(s.question)
            if s.status == REFUSED:
                false_refusals += 1

    return ExistingMetrics(
        retrieval_hit_rate=_rate(hits, n_answerable),
        correct_refusal_rate=_rate(correct_refusals, n_unanswerable),
        false_refusal_rate=_rate(false_refusals, n_answerable),
        retrieval_hits=hits,
        n_answerable=n_answerable,
        correct_refusals=correct_refusals,
        n_unanswerable=n_unanswerable,
        false_refusals=false_refusals,
        missed_questions=missed,
    )


# ---------------------------------------------------------------------------
# Ragas metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RagasMetrics:
    context_recall: float | None
    faithfulness: float | None
    answer_correctness: float | None
    answer_relevancy: float | None


def _nan_to_none(value: float) -> float | None:
    return None if math.isnan(value) else value


def _mean_or_none(scores: list[float]) -> float | None:
    if not scores:
        return None
    finite = [s for s in scores if not math.isnan(s)]
    return sum(finite) / len(finite) if finite else None


def _ragas_evaluate(dataset: Any, settings: "EvalSettings") -> Any:
    """Build Ragas LLM/embeddings and call evaluate; monkeypatchable in tests."""
    from langchain_anthropic import ChatAnthropic
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        answer_correctness,
        answer_relevancy,
        context_recall,
        faithfulness,
    )

    anthropic_key = settings.anthropic_api_key
    llm = LangchainLLMWrapper(
        ChatAnthropic(
            model=settings.generation_model,
            anthropic_api_key=anthropic_key.get_secret_value()  # type: ignore[arg-type]
            if anthropic_key
            else "placeholder",
        )
    )

    from langchain_voyageai import VoyageAIEmbeddings
    from pydantic import SecretStr

    voyage_key = settings.voyage_api_key
    embeddings = LangchainEmbeddingsWrapper(
        VoyageAIEmbeddings(
            model=settings.embedding_model,
            voyage_api_key=SecretStr(
                voyage_key.get_secret_value() if voyage_key else "placeholder"
            ),
        )
    )

    return evaluate(
        dataset,
        metrics=[context_recall, faithfulness, answer_correctness, answer_relevancy],
        llm=llm,
        embeddings=embeddings,
        show_progress=False,
    )


def compute_ragas_metrics(
    samples: Sequence[EvalSample],
    settings: EvalSettings,
) -> RagasMetrics:
    """Compute Ragas LLM-as-judge metrics for samples that have expected_answer."""
    from ragas import EvaluationDataset, SingleTurnSample

    scorable = [s for s in samples if s.expected_answer is not None]
    if not scorable:
        return RagasMetrics(None, None, None, None)

    ragas_samples = [
        SingleTurnSample(
            user_input=s.question,
            response=s.answer,
            retrieved_contexts=s.retrieved_contexts,
            reference=s.expected_answer,
        )
        for s in scorable
    ]
    dataset = EvaluationDataset(samples=ragas_samples)
    result = _ragas_evaluate(dataset, settings)

    def _get(metric_name: str) -> float | None:
        try:
            scores: list[float] = result[metric_name]
        except (KeyError, TypeError):
            return None
        return _mean_or_none(scores)

    return RagasMetrics(
        context_recall=_get("context_recall"),
        faithfulness=_get("faithfulness"),
        answer_correctness=_get("answer_correctness"),
        answer_relevancy=_get("answer_relevancy"),
    )
