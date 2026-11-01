"""Deterministic stand-ins for the providers: no network, no randomness, no clock.

Fakes rather than mocks: they behave like the real thing (similar texts are close,
answers quote their source), so tests exercise the real pipeline logic.
"""

import hashlib
import math
import re
from collections.abc import Sequence

from explorai_ai.models import (
    GeneratedAnswer,
    PromptSource,
    RawCitation,
    RerankHit,
    Usage,
)

_STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "is",
        "are",
        "was",
        "were",
        "be",
        "do",
        "does",
        "did",
        "what",
        "which",
        "who",
        "how",
        "when",
        "where",
        "why",
        "can",
        "i",
        "you",
        "we",
        "it",
        "and",
        "or",
        "with",
        "about",
        "my",
        "me",
    ]
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def tokenize(text: str) -> list[str]:
    """Lowercase and split on non-alphanumeric characters."""
    return [t for t in re.split(r"[^0-9a-z]+", text.lower()) if t]


def _bucket(token: str, dimension: int) -> int:
    digest = hashlib.sha256(token.encode()).digest()
    return int.from_bytes(digest[:8], "big") % dimension


class FakeEmbedder:
    model = "fake-embedding"

    def __init__(self, dimension: int = 64) -> None:
        self.dimension = dimension

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in tokenize(text):
            vector[_bucket(token, self.dimension)] += 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector] if norm else vector

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


def _overlap(question_words: set[str], text: str) -> float:
    if not question_words:
        return 0.0
    return len(question_words & set(tokenize(text))) / len(question_words)


class FakeReranker:
    def rerank(
        self, query: str, documents: Sequence[str], top_k: int
    ) -> list[RerankHit]:
        words = set(tokenize(query))
        hits = [RerankHit(i, _overlap(words, doc)) for i, doc in enumerate(documents)]
        hits.sort(key=lambda hit: (-hit.score, hit.index))
        return hits[:top_k]


class FakeGenerator:
    refusal_overlap = 0.3

    def rewrite_queries(self, question: str, max_queries: int) -> list[str]:
        words = [t for t in tokenize(question) if t not in _STOP_WORDS]
        rewritten = " ".join(words)
        return [rewritten][:max_queries] if rewritten else []

    def answer(self, question: str, sources: Sequence[PromptSource]) -> GeneratedAnswer:
        words = set(tokenize(question))
        best: PromptSource | None = None
        best_share = 0.0
        for source in sources:
            share = _overlap(words, source.text)
            if share > best_share:
                best, best_share = source, share
        question_tokens = len(tokenize(question))
        if best is None or best_share < self.refusal_overlap:
            return GeneratedAnswer("insufficient", "", [], Usage(question_tokens, 0))
        sentence = _SENTENCE_END.split(best.text.strip(), maxsplit=1)[0]
        return GeneratedAnswer(
            "answered",
            f"According to [{best.number}]: {sentence}",
            [RawCitation(best.number, sentence)],
            Usage(question_tokens, len(tokenize(sentence))),
        )
