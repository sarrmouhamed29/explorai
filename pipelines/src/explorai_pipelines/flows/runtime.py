"""Shared collaborators of the flows, built once per process.

Tasks call ``build_runtime()`` instead of receiving these objects as parameters:
Prefect inspects task inputs, and a database engine or HTTP client is not data.
Tests replace ``build_runtime`` with a function returning fakes.
"""

import os
from dataclasses import dataclass
from functools import lru_cache

import httpx
from qdrant_client import QdrantClient

from explorai_ai.config import RagSettings
from explorai_ai.factory import build_providers
from explorai_ai.index import QdrantIndex
from explorai_ai.providers.base import Embedder
from explorai_db.blobs import BlobStore, LocalBlobStore
from explorai_db.session import make_engine, make_session_factory
from explorai_pipelines.config import PipelineSettings, get_settings
from explorai_pipelines.netsafe import Resolver, default_resolver
from explorai_pipelines.repository import Repository
from explorai_pipelines.sources.arxiv import ArxivSource

HTTP_TIMEOUT_SECONDS = 30.0
RAG_SWITCH = "RAG_PROVIDER"


class RagNotConfigured(RuntimeError):
    """The indexing flows were started without the RAG settings."""

    def __init__(self) -> None:
        super().__init__(
            f"RAG is not configured: set {RAG_SWITCH} (and QDRANT_URL, "
            "QDRANT_API_KEY) to run index-pending or prune-index"
        )


@dataclass(frozen=True)
class RagRuntime:
    settings: RagSettings
    embedder: Embedder
    index: QdrantIndex


@dataclass(frozen=True)
class Runtime:
    settings: PipelineSettings
    repo: Repository
    store: BlobStore
    client: httpx.Client
    # One instance per process: its request spacing is shared by every task.
    arxiv: ArxivSource
    resolve: Resolver = default_resolver
    # None when RAG_PROVIDER is unset: ingestion runs without the vector index.
    rag: RagRuntime | None = None


def rag_configured() -> bool:
    """Whether the RAG settings are present (``RAG_PROVIDER`` set and not blank)."""
    return bool(os.environ.get(RAG_SWITCH, "").strip())


def build_rag_runtime() -> RagRuntime | None:
    """The embedder and the index, or None when RAG is not configured.

    Nothing connects here: the Qdrant client and the provider HTTP client open
    connections on first use, so an unreachable service fails the pass that
    needs it, not the start of every flow. Invalid RAG settings fail fast with a
    message naming the variable (never its value).
    """
    if not rag_configured():
        return None
    settings = RagSettings.from_env()
    api_key = settings.qdrant_api_key
    client = QdrantClient(
        url=settings.qdrant_url,
        api_key=api_key.get_secret_value() if api_key else None,
        timeout=int(settings.provider_timeout_seconds),
    )
    return RagRuntime(
        settings=settings,
        embedder=build_providers(settings).embedder,
        index=QdrantIndex(client),
    )


def require_rag(rt: Runtime) -> RagRuntime:
    if rt.rag is None:
        raise RagNotConfigured
    return rt.rag


@lru_cache
def build_runtime() -> Runtime:
    settings = get_settings()
    engine = make_engine(str(settings.database_url))
    client = httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
    return Runtime(
        settings=settings,
        repo=Repository(make_session_factory(engine)),
        store=LocalBlobStore(settings.blob_dir),
        client=client,
        arxiv=ArxivSource(client, settings),
        rag=build_rag_runtime(),
    )
