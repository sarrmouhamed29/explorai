import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from explorai_backend.config import Settings, get_settings
from explorai_backend.main import create_app

REDIS_URL = "redis://localhost:6379/0"
SECRET = "TOPSECRET-pw"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_ENV", "DATABASE_URL", "REDIS_URL", "CHECK_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


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

    with pytest.raises(ValidationError) as exc_info, TestClient(create_app()):
        pass

    assert "database_url" in str(exc_info.value)
    assert SECRET not in str(exc_info.value)


def test_startup_succeeds_with_valid_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")

    with TestClient(create_app()) as client:
        assert client.get("/health/live").status_code == 200


def test_get_settings_returns_the_cached_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")

    first = get_settings()

    assert get_settings() is first
    assert isinstance(first, Settings)
