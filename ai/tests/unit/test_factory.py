import httpx
import pytest
from pydantic import SecretStr, ValidationError

from explorai_ai.config import RagSettings
from explorai_ai.factory import build_providers
from explorai_ai.fakes import FakeEmbedder, FakeGenerator, FakeReranker
from explorai_ai.providers.anthropic_generator import AnthropicGenerator
from explorai_ai.providers.voyage import VoyageEmbedder, VoyageReranker


def real_settings(**overrides: object) -> RagSettings:
    values: dict[str, object] = {
        "provider": "real",
        "anthropic_api_key": SecretStr("a-key"),
        "voyage_api_key": SecretStr("v-key"),
    }
    values.update(overrides)
    return RagSettings(**values)  # type: ignore[arg-type]


def test_fake_provider_returns_the_fakes_with_the_configured_dimension() -> None:
    providers = build_providers(RagSettings(provider="fake", embedding_dimension=32))
    assert isinstance(providers.embedder, FakeEmbedder)
    assert providers.embedder.dimension == 32
    assert isinstance(providers.reranker, FakeReranker)
    assert isinstance(providers.generator, FakeGenerator)


def test_real_provider_wires_the_adapters_from_settings() -> None:
    settings = real_settings(embedding_dimension=512, embed_batch_size=16)
    providers = build_providers(
        settings, http_client=httpx.Client(), anthropic_client=object()
    )
    assert isinstance(providers.embedder, VoyageEmbedder)
    assert providers.embedder.model == "voyage-4"
    assert providers.embedder.dimension == 512
    assert isinstance(providers.reranker, VoyageReranker)
    assert providers.reranker.model == "rerank-2.5"
    assert isinstance(providers.generator, AnthropicGenerator)


def test_real_provider_builds_default_clients_with_the_settings_timeout() -> None:
    providers = build_providers(real_settings(provider_timeout_seconds=9.0))
    assert isinstance(providers.generator, AnthropicGenerator)
    client = providers.generator._client
    assert client.max_retries == 0
    assert client.timeout == 9.0
    http = providers.embedder._client  # type: ignore[attr-defined]
    assert http.timeout.read == 9.0


def test_real_provider_without_keys_fails_at_settings_level() -> None:
    with pytest.raises(ValidationError) as caught:
        RagSettings(provider="real")
    assert "ANTHROPIC_API_KEY" in str(caught.value)


def test_default_models_are_the_verified_ones() -> None:
    settings = RagSettings(provider="fake")
    assert settings.embedding_model == "voyage-4"
    assert settings.embedding_dimension == 1024
    assert settings.rerank_model == "rerank-2.5"
    assert settings.generation_model == "claude-sonnet-5-5"
    assert settings.rewrite_model == "claude-haiku-5-5"
