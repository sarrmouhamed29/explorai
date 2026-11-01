import pytest
from pydantic import ValidationError

from explorai_ai.config import RagSettings, get_rag_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "RAG_PROVIDER",
        "ANTHROPIC_API_KEY",
        "VOYAGE_API_KEY",
        "QDRANT_API_KEY",
        "QDRANT_URL",
        "RAG_DENSE_CANDIDATES",
    ):
        monkeypatch.delenv(name, raising=False)
    get_rag_settings.cache_clear()


def test_provider_is_required() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RagSettings.from_env()
    assert "provider" in str(excinfo.value)


def test_fake_provider_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_PROVIDER", "fake")
    settings = RagSettings.from_env()
    assert settings.provider == "fake"
    assert settings.anthropic_api_key is None
    assert settings.final_candidates == 8


def test_real_provider_names_missing_keys_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_PROVIDER", "real")
    with pytest.raises(ValidationError) as excinfo:
        RagSettings.from_env()
    message = str(excinfo.value)
    assert "anthropic_api_key" in message
    assert "voyage_api_key" in message


def test_real_provider_with_keys_hides_them(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_PROVIDER", "real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-anthropic")
    monkeypatch.setenv("VOYAGE_API_KEY", "sk-secret-voyage")
    settings = RagSettings.from_env()
    assert "sk-secret" not in repr(settings)
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-secret-anthropic"


def test_validation_error_never_echoes_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_PROVIDER", "real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-anthropic")
    monkeypatch.setenv("RAG_DENSE_CANDIDATES", "-7")
    with pytest.raises(ValidationError) as excinfo:
        RagSettings.from_env()
    assert "sk-secret" not in str(excinfo.value)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_non_positive_limits_are_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("RAG_PROVIDER", "fake")
    monkeypatch.setenv("RAG_DENSE_CANDIDATES", value)
    with pytest.raises(ValidationError):
        RagSettings.from_env()


@pytest.mark.parametrize(
    "name", ["RAG_PROVIDER_TIMEOUT_SECONDS", "RAG_REFUSAL_THRESHOLD"]
)
@pytest.mark.parametrize("value", ["inf", "nan", "0"])
def test_non_finite_floats_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv("RAG_PROVIDER", "fake")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        RagSettings.from_env()
