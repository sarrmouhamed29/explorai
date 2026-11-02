import pytest

from explorai_ai.measure import Outcome
from explorai_ai.models import Usage
from explorai_eval.dataset import Question
from explorai_eval.runner import run_evaluation


class _FakePipeline:
    def ask(self, question: str) -> Outcome:
        return Outcome(status="answered", kept_texts=["some text"], usage=Usage(1, 1))


@pytest.fixture
def fake_pipeline() -> _FakePipeline:
    return _FakePipeline()


@pytest.fixture
def questions() -> list[Question]:
    return [
        Question(
            question="What is X?",
            paper="abc",
            expected_quote="X is Y",
            expected_answer="Y",
        ),
        Question(
            question="Who won?",
            paper="none",
            expected_quote=None,
            expected_answer=None,
        ),
    ]


def test_run_evaluation_returns_one_sample_per_question(
    fake_pipeline: _FakePipeline, questions: list[Question]
) -> None:
    samples = run_evaluation(questions, fake_pipeline)
    assert len(samples) == len(questions)


def test_sample_latency_is_positive(
    fake_pipeline: _FakePipeline, questions: list[Question]
) -> None:
    samples = run_evaluation(questions[:1], fake_pipeline)
    assert samples[0].latency_s > 0


def test_sample_preserves_expected_fields(
    fake_pipeline: _FakePipeline, questions: list[Question]
) -> None:
    samples = run_evaluation(questions[:1], fake_pipeline)
    assert samples[0].question == questions[0].question
    assert samples[0].expected_quote == questions[0].expected_quote
    assert samples[0].expected_answer == questions[0].expected_answer


def test_sample_token_counts_are_non_negative(
    fake_pipeline: _FakePipeline, questions: list[Question]
) -> None:
    samples = run_evaluation(questions[:1], fake_pipeline)
    assert samples[0].input_tokens >= 0
    assert samples[0].output_tokens >= 0
