"""Wiring: pick the fakes or the real adapters from settings."""

from dataclasses import dataclass
from typing import Any

import anthropic
import httpx

from explorai_ai.config import RagSettings
from explorai_ai.fakes import FakeEmbedder, FakeGenerator, FakeReranker
from explorai_ai.providers.anthropic_generator import AnthropicGenerator
from explorai_ai.providers.base import Embedder, Generator, Reranker
from explorai_ai.providers.voyage import VoyageEmbedder, VoyageReranker


@dataclass(frozen=True)
class Providers:
    embedder: Embedder
    reranker: Reranker
    generator: Generator


def build_providers(
    settings: RagSettings,
    *,
    http_client: httpx.Client | None = None,
    anthropic_client: Any = None,
) -> Providers:
    if settings.provider == "fake":
        return Providers(
            embedder=FakeEmbedder(dimension=settings.embedding_dimension),
            reranker=FakeReranker(),
            generator=FakeGenerator(),
        )
    # Settings validation guarantees both keys for the real provider.
    assert settings.voyage_api_key is not None
    assert settings.anthropic_api_key is not None
    timeout = settings.provider_timeout_seconds
    http = http_client or httpx.Client(timeout=timeout)
    claude = anthropic_client or anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=timeout,
        max_retries=0,  # retry_transient is the only retry layer
    )
    return Providers(
        embedder=VoyageEmbedder(
            http,
            api_key=settings.voyage_api_key,
            model=settings.embedding_model,
            dimension=settings.embedding_dimension,
            batch_size=settings.embed_batch_size,
        ),
        reranker=VoyageReranker(
            http, api_key=settings.voyage_api_key, model=settings.rerank_model
        ),
        generator=AnthropicGenerator(
            claude,
            model=settings.generation_model,
            rewrite_model=settings.rewrite_model,
            timeout=timeout,
        ),
    )
