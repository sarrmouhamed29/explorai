"""Shared collaborators of the flows, built once per process.

Tasks call ``build_runtime()`` instead of receiving these objects as parameters:
Prefect inspects task inputs, and a database engine or HTTP client is not data.
Tests replace ``build_runtime`` with a function returning fakes.
"""

from dataclasses import dataclass
from functools import lru_cache

import httpx

from explorai_db.blobs import BlobStore, LocalBlobStore
from explorai_db.session import make_engine, make_session_factory
from explorai_pipelines.config import PipelineSettings, get_settings
from explorai_pipelines.netsafe import Resolver, default_resolver
from explorai_pipelines.repository import Repository
from explorai_pipelines.sources.arxiv import ArxivSource

HTTP_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class Runtime:
    settings: PipelineSettings
    repo: Repository
    store: BlobStore
    client: httpx.Client
    # One instance per process: its request spacing is shared by every task.
    arxiv: ArxivSource
    resolve: Resolver = default_resolver


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
    )
