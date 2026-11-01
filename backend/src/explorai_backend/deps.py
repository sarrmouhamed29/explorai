"""Cached infrastructure dependencies, injectable with ``Depends``.

This is the only backend module allowed to build an engine or a session factory,
and it never hands the factory out: request code gets ``ScopedSessions``, whose
only method opens an identity-scoped transaction (enforced by
``tests/unit/test_architecture.py``).
"""

from functools import lru_cache
from typing import Annotated

import redis
from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

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


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(str(get_settings().redis_url))


@lru_cache
def get_blob_store() -> BlobStore:
    return LocalBlobStore(get_settings().blob_dir)
