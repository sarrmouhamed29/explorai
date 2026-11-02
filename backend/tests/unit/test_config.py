from pathlib import Path

import pytest
from pydantic import ValidationError

from explorai_backend.config import Settings

PG_URL = "postgresql://u:p@localhost:5432/db"
REDIS_URL = "redis://localhost:6379/0"
ENV_NAMES = (
    "APP_ENV",
    "DATABASE_URL",
    "REDIS_URL",
    "CHECK_TIMEOUT_SECONDS",
    "BLOB_DIR",
    "ALLOWED_ORIGINS",
    "UPLOAD_MAX_BYTES",
    "SESSION_COOKIE_SECURE",
    "SESSION_IDLE_SECONDS",
    "LOGIN_WINDOW_SECONDS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_settings_loads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BLOB_DIR", "blobs")

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
    monkeypatch.setenv("BLOB_DIR", "blobs")

    with pytest.raises(ValidationError) as exc_info:
        Settings.from_env()

    assert "database_url" in str(exc_info.value)


def test_non_postgres_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@localhost:3306/db")
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BLOB_DIR", "blobs")

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
    monkeypatch.setenv("BLOB_DIR", "blobs")
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
    monkeypatch.setenv("BLOB_DIR", "blobs")
    monkeypatch.setenv("CHECK_TIMEOUT_SECONDS", str(value))

    assert Settings.from_env().check_timeout_seconds == value


def _valid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BLOB_DIR", "blobs")


def test_core_backend_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _valid_env(monkeypatch)

    settings = Settings.from_env()

    assert settings.blob_dir == Path("blobs")
    assert settings.upload_max_bytes == 20_000_000
    assert settings.allowed_origins == ["http://127.0.0.1:5173"]
    assert settings.session_cookie_secure is True
    assert settings.session_idle_seconds == 86_400
    assert settings.session_max_seconds == 604_800
    assert settings.login_max_failures_per_email == 5
    assert settings.login_max_failures_per_ip == 20
    assert settings.login_window_seconds == 900
    assert settings.upload_max_per_hour == 20
    assert settings.max_documents_per_user == 200


def test_missing_blob_dir_raises_and_names_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _valid_env(monkeypatch)
    monkeypatch.delenv("BLOB_DIR")

    with pytest.raises(ValidationError) as exc_info:
        Settings.from_env()

    assert "blob_dir" in str(exc_info.value)


def test_allowed_origins_parse_from_comma_separated_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _valid_env(monkeypatch)
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://a.test, http://b.test ,")

    assert Settings.from_env().allowed_origins == ["http://a.test", "http://b.test"]


def test_session_cookie_secure_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _valid_env(monkeypatch)
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")

    assert Settings.from_env().session_cookie_secure is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("UPLOAD_MAX_BYTES", "0"),
        ("SESSION_IDLE_SECONDS", "-1"),
        ("LOGIN_WINDOW_SECONDS", "0"),
    ],
)
def test_limits_must_be_positive(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _valid_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError) as exc_info:
        Settings.from_env()

    assert name.lower() in str(exc_info.value)
