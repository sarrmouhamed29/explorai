"""Plain value objects shared by the RAG logic and the provider adapters."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import UUID

if TYPE_CHECKING:
    # citations imports this module; the annotation below is never evaluated.
    from explorai_ai.citations import Citation


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class RerankHit:
    index: int
    score: float


@dataclass(frozen=True)
class PromptSource:
    number: int
    title: str | None
    heading: str | None
    text: str


@dataclass(frozen=True)
class RawCitation:
    source: int
    quote: str


@dataclass(frozen=True)
class GeneratedAnswer:
    status: Literal["answered", "insufficient"]
    answer: str
    citations: list[RawCitation]
    usage: Usage


@dataclass(frozen=True)
class ScoredId:
    id: UUID
    score: float


@dataclass(frozen=True)
class AnswerResult:
    """What ``AnswerPipeline.ask`` returns: an answer, or an explicit refusal.

    ``insufficient_evidence`` always has ``answer=None`` and no citations; an
    ``answered`` result always has at least one verified citation.
    """

    status: Literal["answered", "insufficient_evidence"]
    answer: str | None
    citations: list["Citation"]
    usage: Usage
