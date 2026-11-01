import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from explorai_backend import deps
from explorai_backend.config import Settings, get_settings
from explorai_backend.main import create_app

REDIS_URL = "redis://localhost:6379/0"
SECRET = "TOPSECRET-pw"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "APP_ENV",
        "DATABASE_URL",
        "REDIS_URL",
        "CHECK_TIMEOUT_SECONDS",
        "BLOB_DIR",
        "RAG_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    deps.get_rag_settings.cache_clear()


def test_startup_refuses_to_run_without_configuration() -> None:
    with pytest.raises(ValidationError) as exc_info, TestClient(create_app()):
        pass

    assert "database_url" in str(exc_info.value)


def test_startup_error_never_echoes_the_secret_in_a_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"mysql://u:{SECRET}@127.0.0.1/db")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BLOB_DIR", "blobs")

    with pytest.raises(ValidationError) as exc_info, TestClient(create_app()):
        pass

    assert "database_url" in str(exc_info.value)
    assert SECRET not in str(exc_info.value)


def test_startup_refuses_to_run_without_rag_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BLOB_DIR", "blobs")

    # Invalid RAG configuration fails at startup, not on the first question, and
    # the message names the environment variable, not the pydantic field.
    with (
        pytest.raises(deps.RagConfigurationError) as exc_info,
        TestClient(create_app()),
    ):
        pass

    assert "RAG_PROVIDER" in str(exc_info.value)


def test_startup_names_unprefixed_variables_and_hides_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BLOB_DIR", "blobs")
    monkeypatch.setenv("RAG_PROVIDER", "real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setenv("RAG_PROVIDER_TIMEOUT_SECONDS", "-" + SECRET)

    with (
        pytest.raises(deps.RagConfigurationError) as exc_info,
        TestClient(create_app()),
    ):
        pass

    message = str(exc_info.value)
    assert "RAG_PROVIDER_TIMEOUT_SECONDS" in message
    assert SECRET not in message
    assert exc_info.value.__cause__ is None  # the ValidationError is not chained


def test_rag_error_names_the_unprefixed_key_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_PROVIDER", "real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    with pytest.raises(deps.RagConfigurationError) as exc_info:
        deps.get_rag_settings()

    message = str(exc_info.value)
    assert "VOYAGE_API_KEY" in message
    assert SECRET not in message


def test_env_names_follow_the_settings_aliases() -> None:
    assert deps.rag_env_name("provider") == "RAG_PROVIDER"
    assert deps.rag_env_name("ask_max_per_hour") == "RAG_ASK_MAX_PER_HOUR"
    for field, variable in [
        ("anthropic_api_key", "ANTHROPIC_API_KEY"),
        ("voyage_api_key", "VOYAGE_API_KEY"),
        ("qdrant_url", "QDRANT_URL"),
        ("qdrant_api_key", "QDRANT_API_KEY"),
    ]:
        assert deps.rag_env_name(field) == variable


def test_startup_succeeds_with_valid_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BLOB_DIR", "blobs")
    monkeypatch.setenv("RAG_PROVIDER", "fake")

    with TestClient(create_app()) as client:
        assert client.get("/health/live").status_code == 200


def test_get_settings_returns_the_cached_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BLOB_DIR", "blobs")

    first = get_settings()

    assert get_settings() is first
    assert isinstance(first, Settings)
