"""Ports: what the RAG logic needs from the outside world (hexagonal architecture)."""

from collections.abc import Sequence
from typing import Protocol

from explorai_ai.models import GeneratedAnswer, PromptSource, RerankHit


class Embedder(Protocol):
    model: str
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    def rerank(
        self, query: str, documents: Sequence[str], top_k: int
    ) -> list[RerankHit]:
        """Return at most top_k hits sorted by score, best first."""
        ...


class Generator(Protocol):
    def rewrite_queries(self, question: str, max_queries: int) -> list[str]: ...

    def answer(
        self, question: str, sources: Sequence[PromptSource]
    ) -> GeneratedAnswer: ...
