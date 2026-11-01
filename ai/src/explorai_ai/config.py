"""RAG settings: read from the environment, fail fast, never echo a secret."""

from functools import lru_cache
from typing import Any, Literal, Self

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from explorai_ai.index import QdrantIndex
from explorai_db.models import INDEX_NAME_MAX_LENGTH


class RagSettings(BaseSettings):
    # Validation errors name the field but never echo its value (API keys).
    # Own settings use the RAG_ prefix; provider credentials keep their usual names.
    model_config = SettingsConfigDict(
        hide_input_in_errors=True, env_prefix="RAG_", populate_by_name=True
    )

    provider: Literal["real", "fake"]
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY")
    )
    voyage_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("VOYAGE_API_KEY")
    )
    qdrant_url: str = Field(
        default="http://127.0.0.1:6333", validation_alias=AliasChoices("QDRANT_URL")
    )
    qdrant_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("QDRANT_API_KEY")
    )
    # Voyage models, checked against docs.voyageai.com on 2026-11-01: voyage-4
    # supports output_dimension 256/512/1024/2048 (1024 is its default).
    embedding_model: str = "voyage-4"
    embedding_dimension: int = Field(default=1024, gt=0)
    rerank_model: str = "rerank-2.5"
    generation_model: str = "claude-sonnet-5-5"
    rewrite_model: str = "claude-haiku-5-5"
    rewrite_enabled: bool = True
    rerank_enabled: bool = True
    dense_candidates: int = Field(default=50, gt=0)
    sparse_candidates: int = Field(default=50, gt=0)
    fused_candidates: int = Field(default=30, gt=0)
    final_candidates: int = Field(default=8, gt=0)
    refusal_threshold: float = Field(default=0.2, gt=0, allow_inf_nan=False)
    chunk_words: int = Field(default=400, gt=0)
    chunk_overlap_words: int = Field(default=60, gt=0)
    max_chunks_per_document: int = Field(default=500, gt=0)
    embed_batch_size: int = Field(default=64, gt=0)
    provider_timeout_seconds: float = Field(default=20.0, gt=0, allow_inf_nan=False)
    ask_max_per_hour: int = Field(default=30, gt=0)
    question_max_chars: int = Field(default=2000, gt=0)

    @model_validator(mode="after")
    def _index_name_fits(self) -> Self:
        # The index name is stored in chunk_index_entries.index_name; a longer one
        # would fail every insert, so refuse it at startup instead.
        if len(self.index_name) > INDEX_NAME_MAX_LENGTH:
            raise ValueError(
                "RAG_EMBEDDING_MODEL is too long: the index name would exceed "
                f"{INDEX_NAME_MAX_LENGTH} characters"
            )
        return self

    @property
    def index_name(self) -> str:
        """The vector index of these settings (collection and entries name)."""
        return QdrantIndex.collection_name(
            self.embedding_model, self.embedding_dimension
        )

    @model_validator(mode="after")
    def _real_provider_needs_keys(self) -> Self:
        if self.provider == "real":
            required = {
                "ANTHROPIC_API_KEY": self.anthropic_api_key,
                "VOYAGE_API_KEY": self.voyage_api_key,
            }
            # Missing, empty or blank all count as missing (Compose turns an unset
            # ${KEY:-} into an empty string).
            missing = [
                variable
                for variable, key in required.items()
                if key is None or not key.get_secret_value().strip()
            ]
            if missing:
                raise ValueError(
                    "provider 'real' requires a non-empty " + ", ".join(missing)
                )
        return self

    @classmethod
    def from_env(cls) -> "RagSettings":
        """Build settings purely from the environment (see backend Settings)."""
        no_explicit_values: dict[str, Any] = {}
        return cls(**no_explicit_values)


@lru_cache
def get_rag_settings() -> RagSettings:
    return RagSettings.from_env()
