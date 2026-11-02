import pytest
from pydantic import ValidationError

from explorai_pipelines.config import PipelineSettings

PG_URL = "postgresql://u:s3cr3t-pw@127.0.0.1:5432/db"
NAMES = (
    "DATABASE_URL",
    "BLOB_DIR",
    "CONTACT_EMAIL",
    "MAX_DOWNLOAD_BYTES",
    "EXTRACT_TIMEOUT_SECONDS",
    "ARXIV_MIN_INTERVAL_SECONDS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setenv("BLOB_DIR", "/tmp/blobs")
    monkeypatch.setenv("CONTACT_EMAIL", "me@example.org")


def test_defaults_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    settings = PipelineSettings.from_env()
    assert settings.max_download_bytes == 50_000_000
    assert settings.extract_timeout_seconds == 60
    assert settings.arxiv_min_interval_seconds == 3.0


def test_missing_contact_email_names_variable_and_hides_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    monkeypatch.setenv("BLOB_DIR", "/tmp/blobs")
    with pytest.raises(ValidationError) as exc_info:
        PipelineSettings.from_env()
    message = str(exc_info.value)
    assert "contact_email" in message
    assert "s3cr3t-pw" not in message


@pytest.mark.parametrize(
    "name",
    ["MAX_DOWNLOAD_BYTES", "EXTRACT_TIMEOUT_SECONDS", "ARXIV_MIN_INTERVAL_SECONDS"],
)
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_non_positive_or_non_finite_limits_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        PipelineSettings.from_env()
