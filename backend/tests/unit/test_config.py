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

    settings = Settings.from_env()

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
        Settings.from_env()

    assert "database_url" in str(exc_info.value)


def test_non_postgres_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@localhost:3306/db")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")

    with pytest.raises(ValidationError):
        Settings.from_env()


def test_invalid_app_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValidationError):
        Settings.from_env()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "31"])
def test_check_timeout_out_of_bounds_is_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CHECK_TIMEOUT_SECONDS", value)

    with pytest.raises(ValidationError) as exc_info:
        Settings.from_env()

    assert "check_timeout_seconds" in str(exc_info.value)


@pytest.mark.parametrize("value", [0.5, 30])
def test_check_timeout_within_bounds_is_accepted(
    monkeypatch: pytest.MonkeyPatch, value: float
) -> None:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CHECK_TIMEOUT_SECONDS", str(value))

    assert Settings.from_env().check_timeout_seconds == value
