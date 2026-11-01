"""Plain value objects shared by the RAG logic and the provider adapters."""

from dataclasses import dataclass
from typing import Literal


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
