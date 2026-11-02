"""Cached infrastructure dependencies, injectable with ``Depends``.

This is the only backend module allowed to build an engine or a session factory,
and it never hands the factory out: request code gets ``ScopedSessions``, whose
only method opens an identity-scoped transaction (enforced by
``tests/unit/test_architecture.py``).
"""

import math
from functools import lru_cache
from typing import Annotated

import redis
from fastapi import Depends
from pydantic import AliasChoices, ValidationError
from qdrant_client import QdrantClient
from sqlalchemy.orm import Session, sessionmaker

from explorai_ai.config import RagSettings
from explorai_ai.factory import Providers, build_providers
from explorai_ai.index import QdrantIndex
from explorai_ai.pipeline import AnswerPipeline
from explorai_ai.retriever import Retriever
from explorai_ai.store import ChunkStore
from explorai_backend.config import get_settings
from explorai_db.blobs import BlobStore, LocalBlobStore
from explorai_db.rls import ScopedSessions
from explorai_db.session import make_engine, make_session_factory


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    engine = make_engine(str(get_settings().database_url))
    return make_session_factory(engine)


def get_scoped_sessions(
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> ScopedSessions:
    # Built per request (one object, no I/O) so that overriding
    # get_session_factory in tests still reaches every repository.
    return ScopedSessions(factory)


# A Redis that accepts the connection but never answers would otherwise hang the
# request forever; with a timeout redis-py raises and the route answers 503.
REDIS_TIMEOUT_SECONDS = 2


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(
        str(get_settings().redis_url),
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
    )


@lru_cache
def get_blob_store() -> BlobStore:
    return LocalBlobStore(get_settings().blob_dir)


class RagConfigurationError(Exception):
    """Invalid RAG settings; the message names environment variables, no values."""


def rag_env_name(field: str) -> str:
    """The environment variable of a ``RagSettings`` field.

    The provider credentials and the Qdrant settings keep their usual names
    (``validation_alias``); every other field reads ``RAG_<FIELD>``.
    """
    alias = RagSettings.model_fields[field].validation_alias
    if isinstance(alias, AliasChoices) and isinstance(alias.choices[0], str):
        return alias.choices[0]
    if isinstance(alias, str):
        return alias
    return f"{RagSettings.model_config.get('env_prefix', '')}{field}".upper()


def _describe(error: ValidationError) -> str:
    """One line per problem, from ``loc`` and ``msg`` only, never ``input``.

    A model-level error has an empty ``loc``; its message (written in
    ``explorai_ai.config``) already names the variables it is about.
    """
    problems = []
    for item in error.errors(include_input=False, include_url=False):
        loc = item["loc"]
        field = str(loc[0]) if loc else ""
        if field in RagSettings.model_fields:
            problems.append(f"{rag_env_name(field)}: {item['msg']}")
        elif field:
            # Validated through its alias: the location already is the variable.
            problems.append(f"{field.upper()}: {item['msg']}")
        else:
            problems.append(item["msg"])
    return "invalid RAG configuration: " + "; ".join(problems)


@lru_cache
def get_rag_settings() -> RagSettings:
    """RAG settings from the environment, read once (also called at startup)."""
    try:
        return RagSettings.from_env()
    except ValidationError as error:
        # from None: the chained ValidationError would print field names again
        # and is not needed; this message already says what to fix.
        raise RagConfigurationError(_describe(error)) from None


@lru_cache
def get_qdrant_index() -> QdrantIndex:
    """One Qdrant client per process; it connects on first use, not here."""
    settings = get_rag_settings()
    api_key = settings.qdrant_api_key
    client = QdrantClient(
        url=settings.qdrant_url,
        api_key=api_key.get_secret_value() if api_key else None,
        # Whole seconds only: round up so a sub-second setting never becomes 0.
        timeout=max(1, math.ceil(settings.provider_timeout_seconds)),
        # The default version check is a request at construction: a slow or
        # absent Qdrant would stall the first question. Outages surface on use.
        check_compatibility=False,
    )
    return QdrantIndex(client)


@lru_cache
def get_providers() -> Providers:
    """The model adapters (or the fakes), with their HTTP clients, built once."""
    return build_providers(get_rag_settings())


def get_chunk_store(
    sessions: Annotated[ScopedSessions, Depends(get_scoped_sessions)],
) -> ChunkStore:
    # ChunkStore re-reads chunk texts under RLS through ScopedSessions only.
    return ChunkStore(sessions)


def get_answer_pipeline(
    settings: Annotated[RagSettings, Depends(get_rag_settings)],
    store: Annotated[ChunkStore, Depends(get_chunk_store)],
    index: Annotated[QdrantIndex, Depends(get_qdrant_index)],
    providers: Annotated[Providers, Depends(get_providers)],
) -> AnswerPipeline:
    # Per request (plain objects, no I/O): the store carries this request's
    # ScopedSessions, while the clients inside index and providers are shared.
    retriever = Retriever(
        index=index,
        embedder=providers.embedder,
        reranker=providers.reranker,
        generator=providers.generator,
        store=store,
        settings=settings,
    )
    return AnswerPipeline(retriever, providers.generator, settings)
