"""Metrics for RAG evaluation.

Task 4: existing metrics (hit rate, refusals).
Task 5: Ragas metrics are added here.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from explorai_ai.citations import normalise_for_match
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
