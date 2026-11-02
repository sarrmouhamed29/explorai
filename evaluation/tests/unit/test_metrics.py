"""Tests for existing metrics (hit rate, refusals).

Ragas metric tests are added in Task 5.
"""

import pytest

from explorai_eval.metrics import compute_existing_metrics
from explorai_eval.runner import EvalSample


def _sample(
    *,
    question: str = "Q?",
    status: str = "answered",
    retrieved_contexts: list[str] | None = None,
    expected_quote: str | None = "the answer",
    expected_answer: str | None = None,
) -> EvalSample:
    return EvalSample(
        question=question,
        answer="some answer",
        status=status,
        retrieved_contexts=retrieved_contexts
        if retrieved_contexts is not None
        else ["the answer is here"],
        expected_quote=expected_quote,
        expected_answer=expected_answer,
        latency_s=0.1,
        input_tokens=10,
        output_tokens=5,
    )


@pytest.fixture
def perfect_samples() -> list[EvalSample]:
    return [
        _sample(
            question="Q1?", expected_quote="alpha", retrieved_contexts=["alpha is here"]
        ),
        _sample(
            question="Q2?", expected_quote="beta", retrieved_contexts=["beta found"]
        ),
    ]


@pytest.fixture
def missed_samples() -> list[EvalSample]:
    return [
        _sample(
            question="Q1?",
            expected_quote="alpha",
            retrieved_contexts=["nothing relevant"],
        ),
        _sample(
            question="Q2?", expected_quote="beta", retrieved_contexts=["still nothing"]
        ),
    ]


@pytest.fixture
def unanswerable_only_samples() -> list[EvalSample]:
    return [
        _sample(
            question="Who won?",
            status="insufficient_evidence",
            expected_quote=None,
            retrieved_contexts=[],
        ),
    ]


@pytest.fixture
def refusal_samples() -> list[EvalSample]:
    return [
        _sample(
            question="Who won?",
            status="insufficient_evidence",
            expected_quote=None,
            retrieved_contexts=[],
        ),
    ]


@pytest.fixture
def false_refusal_samples() -> list[EvalSample]:
    return [
        _sample(
            question="Q1?",
            status="insufficient_evidence",
            expected_quote="the answer",
            retrieved_contexts=["the answer"],
        ),
    ]


def test_perfect_hit_rate(perfect_samples: list[EvalSample]) -> None:
    result = compute_existing_metrics(perfect_samples)
    assert result.retrieval_hit_rate == 1.0


def test_zero_hit_rate(missed_samples: list[EvalSample]) -> None:
    result = compute_existing_metrics(missed_samples)
    assert result.retrieval_hit_rate == 0.0


def test_hit_rate_none_when_no_answerable(
    unanswerable_only_samples: list[EvalSample],
) -> None:
    result = compute_existing_metrics(unanswerable_only_samples)
    assert result.retrieval_hit_rate is None


def test_correct_refusal_rate(refusal_samples: list[EvalSample]) -> None:
    result = compute_existing_metrics(refusal_samples)
    assert result.correct_refusal_rate == 1.0


def test_false_refusals_counted(false_refusal_samples: list[EvalSample]) -> None:
    result = compute_existing_metrics(false_refusal_samples)
    assert result.false_refusals == 1
