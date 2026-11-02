"""Twelve-factor configuration: read from the environment, fail fast on bad values."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    # Validation errors name the offending field but never echo its value: a DSN
    # carries the database password and startup errors end up in the logs.
    model_config = SettingsConfigDict(hide_input_in_errors=True)

    app_env: Literal["development", "test", "production"]
    database_url: PostgresDsn
    redis_url: RedisDsn
    # Bounded: 0 would make /health/ready always 503, inf/nan would let it hang.
    check_timeout_seconds: float = Field(default=2.0, gt=0, le=30, allow_inf_nan=False)

    blob_dir: Path
    upload_max_bytes: int = Field(default=20_000_000, gt=0)
    # NoDecode: the value is a comma-separated string, not JSON; see the validator.
    allowed_origins: Annotated[list[str], NoDecode] = ["http://127.0.0.1:5173"]
    session_cookie_secure: bool = True
    session_idle_seconds: int = Field(default=86_400, gt=0)
    session_max_seconds: int = Field(default=604_800, gt=0)
    login_max_failures_per_email: int = Field(default=5, gt=0)
    login_max_failures_per_ip: int = Field(default=20, gt=0)
    login_window_seconds: int = Field(default=900, gt=0)
    upload_max_per_hour: int = Field(default=20, gt=0)
    max_documents_per_user: int = Field(default=200, gt=0)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    # Callers use from_env() instead of Settings(): the pydantic mypy plugin
    # types required fields as required constructor args.
    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings purely from the environment.

        The pydantic mypy plugin types required fields as required __init__
        arguments, but BaseSettings fills them from the environment. Splatting an
        empty mapping expresses "no explicit values" without a type: ignore.
        """
        no_explicit_values: dict[str, Any] = {}
        return cls(**no_explicit_values)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
