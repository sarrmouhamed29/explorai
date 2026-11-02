from typing import Any

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import SettingsConfigDict

from explorai_ai.config import RagSettings


class EvalSettings(RagSettings):
    model_config = SettingsConfigDict(
        hide_input_in_errors=True,
        env_prefix="RAG_",
        populate_by_name=True,
        extra="ignore",
    )

    langfuse_host: str = Field(
        default="", validation_alias=AliasChoices("LANGFUSE_HOST")
    )
    langfuse_public_key: str = Field(
        default="", validation_alias=AliasChoices("LANGFUSE_PUBLIC_KEY")
    )
    langfuse_secret_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("LANGFUSE_SECRET_KEY")
    )

    @classmethod
    def from_env(cls) -> "EvalSettings":
        no_explicit_values: dict[str, Any] = {}
        return cls(**no_explicit_values)
