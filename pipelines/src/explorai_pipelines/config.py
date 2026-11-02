"""Twelve-factor configuration for the pipelines: environment only, fail fast."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    # Validation errors name the offending field but never echo its value: the
    # DSN carries the database password and startup errors end up in the logs.
    model_config = SettingsConfigDict(hide_input_in_errors=True)

    database_url: PostgresDsn
    blob_dir: Path
    # Goes into the User-Agent so that arXiv can reach the operator.
    contact_email: str = Field(min_length=3)
    max_download_bytes: int = Field(default=50_000_000, gt=0)
    extract_timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    arxiv_min_interval_seconds: float = Field(default=3.0, gt=0, allow_inf_nan=False)

    @classmethod
    def from_env(cls) -> "PipelineSettings":
        """Build settings purely from the environment (see backend Settings)."""
        no_explicit_values: dict[str, Any] = {}
        return cls(**no_explicit_values)


@lru_cache
def get_settings() -> PipelineSettings:
    return PipelineSettings.from_env()
