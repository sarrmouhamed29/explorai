"""The Compose stack hands every RAG setting to the backend and the pipelines.

Without it, ``RAG_PROVIDER=real`` cannot start in Compose (no key reaches the
containers), and the two services could disagree on the model or dimension,
which name the index. Read as text: no YAML dependency for one file.
"""

import re
from pathlib import Path

from explorai_ai.config import RagSettings
from explorai_backend.deps import rag_env_name

COMPOSE = Path(__file__).resolve().parents[3] / "infra" / "docker-compose.yml"
ANCHOR = "x-rag-environment: &rag-environment"
# Secrets and connection values are not tuning defaults.
NO_DEFAULT = {"QDRANT_URL", "QDRANT_API_KEY"}


def rag_block() -> dict[str, str]:
    text = COMPOSE.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert ANCHOR in text
    block = text.split(ANCHOR + "\n", 1)[1]
    values: dict[str, str] = {}
    for line in block.split("\n"):
        if not line.startswith("  ") or line.strip().startswith("#"):
            break
        name, value = line.strip().split(": ", 1)
        values[name] = value
    return values


def service_environment(service: str) -> str:
    text = COMPOSE.read_text(encoding="utf-8").replace("\r\n", "\n")
    body = text.split(f"\n  {service}:\n", 1)[1]
    following = re.search(r"\n  [a-z]+:\n", body)
    return body[: following.start()] if following else body


def test_every_rag_setting_is_forwarded_with_its_default() -> None:
    block = rag_block()
    for field, info in RagSettings.model_fields.items():
        name = rag_env_name(field)
        assert name in block, name
        if name in NO_DEFAULT:
            continue
        match = re.fullmatch(r"\$\{" + name + r":-(.*)\}", block[name])
        assert match is not None, block[name]
        default = info.default
        if field == "provider":
            assert match.group(1) == "fake"
        elif default is None:
            assert match.group(1) == "", name  # keys: empty, never a value
        elif isinstance(default, bool):
            assert match.group(1) == str(default).lower(), name
        elif isinstance(default, float):
            assert float(match.group(1)) == default, name
        else:
            assert match.group(1) == str(default), name


def test_backend_and_pipelines_both_merge_the_rag_settings() -> None:
    for service in ("backend", "pipelines"):
        assert "<<: *rag-environment" in service_environment(service), service
