"""Twelve-factor configuration: read from the environment, fail fast on bad values."""

from functools import lru_cache
from typing import Any, Literal

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: Literal["development", "test", "production"]
    database_url: PostgresDsn
    redis_url: RedisDsn
    check_timeout_seconds: float = 2.0

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
