from typing import Any

from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from explorai_ai.config import RagSettings


class EvalSettings(RagSettings):
    model_config = SettingsConfigDict(
        hide_input_in_errors=True,
        env_prefix="RAG_",
        populate_by_name=True,
        extra="ignore",
    )

    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr | None = None

    @classmethod
    def from_env(cls) -> "EvalSettings":
        no_explicit_values: dict[str, Any] = {}
        return cls(**no_explicit_values)
