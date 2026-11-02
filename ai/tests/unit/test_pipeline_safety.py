"""Tests for InputFilter and OutputFilter hooks in AnswerPipeline."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from explorai_ai.config import RagSettings
from explorai_ai.fakes import FakeGenerator
from explorai_ai.models import GeneratedAnswer, PromptSource, Usage
from explorai_ai.pii import PiiScanner
from explorai_ai.pipeline import AnswerPipeline
from explorai_ai.retriever import Candidate, RetrievalResult
from explorai_ai.safety_input import InputDecision, InputFilter, InputFilterResult
from explorai_ai.safety_output import OutputFilter, OutputFilterResult
from explorai_ai.scope import Scope

_USER = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_SETTINGS = RagSettings(provider="fake")


def _candidate(text: str = "Alpha is the first letter.") -> Candidate:
    return Candidate(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        document_title="Test",
        heading="Section",
        text=text,
        score=0.9,
    )


@dataclass
class _ScriptedRetriever:
    result: RetrievalResult
    calls: list[tuple[uuid.UUID, str]] = field(default_factory=list)

    def retrieve(
        self,
        user_id: uuid.UUID,
        question: str,
        scope: Scope = "all",
        document_id: uuid.UUID | None = None,
    ) -> RetrievalResult:
        self.calls.append((user_id, question))
        return self.result


def _retriever_with_candidate() -> _ScriptedRetriever:
    return _ScriptedRetriever(
        result=RetrievalResult(candidates=[_candidate()], best_score=0.9)
    )


class _AlwaysBlock(InputFilter):
    def check(self, question: str) -> InputFilterResult:
        return InputFilterResult(decision=InputDecision.BLOCK, reason="test")


class _AlwaysUnsafe(OutputFilter):
    def check(
        self,
        answer: str,
        citations: list,  # type: ignore[override]
    ) -> OutputFilterResult:
        return OutputFilterResult(safe=False, redacted_answer=None)


class _AlwaysAnsweredGenerator(FakeGenerator):
    def answer(self, question: str, sources: Sequence[PromptSource]) -> GeneratedAnswer:
        from explorai_ai.models import RawCitation

        source = sources[0]
        return GeneratedAnswer(
            status="answered",
            answer=f"According to [{source.number}]: {source.text[:40]}",
            citations=[RawCitation(source.number, source.text[:40])],
            usage=Usage(10, 10),
        )


def test_injection_in_question_returns_insufficient() -> None:
    pipeline = AnswerPipeline(
        _retriever_with_candidate(),
        FakeGenerator(),
        _SETTINGS,
        input_filter=_AlwaysBlock(FakeGenerator()),
    )
    result = pipeline.ask(_USER, "ignored question")
    assert result.status == "insufficient_evidence"


def test_pii_in_answer_returns_insufficient() -> None:
    pipeline = AnswerPipeline(
        _retriever_with_candidate(),
        _AlwaysAnsweredGenerator(),
        _SETTINGS,
        output_filter=_AlwaysUnsafe(PiiScanner()),
    )
    result = pipeline.ask(_USER, "What is alpha?")
    assert result.status == "insufficient_evidence"


def test_no_filters_pipeline_unchanged() -> None:
    pipeline = AnswerPipeline(
        _retriever_with_candidate(),
        FakeGenerator(),
        _SETTINGS,
    )
    result = pipeline.ask(_USER, "What is alpha?")
    assert result.status in ("answered", "insufficient_evidence")
