import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from explorai_ai.measure import Outcome
from explorai_eval.dataset import Question


@dataclass(frozen=True)
class EvalSample:
    question: str
    answer: str
    status: str
    retrieved_contexts: list[str]
    expected_quote: str | None
    expected_answer: str | None
    latency_s: float
    input_tokens: int
    output_tokens: int


class _Asks(Protocol):
    def ask(self, question: str) -> Outcome: ...


def run_evaluation(questions: Sequence[Question], pipeline: _Asks) -> list[EvalSample]:
    """Run the pipeline on each question, returning one EvalSample per question."""
    samples: list[EvalSample] = []
    for q in questions:
        t0 = time.perf_counter()
        outcome = pipeline.ask(q.question)
        latency = time.perf_counter() - t0
        samples.append(
            EvalSample(
                question=q.question,
                answer="\n".join(outcome.kept_texts),
                status=outcome.status,
                retrieved_contexts=list(outcome.kept_texts),
                expected_quote=q.expected_quote,
                expected_answer=q.expected_answer,
                latency_s=latency,
                input_tokens=outcome.usage.input_tokens,
                output_tokens=outcome.usage.output_tokens,
            )
        )
    return samples
