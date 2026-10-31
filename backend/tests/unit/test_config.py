import pytest
from pydantic import ValidationError

from explorai_backend.config import Settings

PG_URL = "postgresql://u:p@localhost:5432/db"
REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_ENV", "DATABASE_URL", "REDIS_URL", "CHECK_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)


def test_settings_loads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")

    settings = Settings()

    assert settings.app_env == "test"
    assert str(settings.database_url) == PG_URL
    assert str(settings.redis_url) == REDIS_URL
    assert settings.check_timeout_seconds == 2.0


def test_missing_database_url_raises_and_names_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert "database_url" in str(exc_info.value)


def test_non_postgres_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@localhost:3306/db")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")

    with pytest.raises(ValidationError):
        Settings()


def test_invalid_app_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValidationError):
        Settings()
