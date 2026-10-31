"""Twelve-factor configuration: read from the environment, fail fast on bad values."""

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Validation errors name the offending field but never echo its value: a DSN
    # carries the database password and startup errors end up in the logs.
    model_config = SettingsConfigDict(hide_input_in_errors=True)

    app_env: Literal["development", "test", "production"]
    database_url: PostgresDsn
    redis_url: RedisDsn
    # Bounded: 0 would make /health/ready always 503, inf/nan would let it hang.
    check_timeout_seconds: float = Field(default=2.0, gt=0, le=30, allow_inf_nan=False)

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
