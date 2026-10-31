"""Twelve-factor configuration: read from the environment, fail fast on invalid values."""

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: Literal["development", "test", "production"]
    database_url: PostgresDsn
    redis_url: RedisDsn
    check_timeout_seconds: float = 2.0


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
