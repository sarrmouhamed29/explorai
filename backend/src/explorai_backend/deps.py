"""Cached infrastructure dependencies, injectable with ``Depends``.

This is the only backend module allowed to build an engine or a session factory;
request code opens transactions through ``explorai_db.rls.scoped_session`` only
(enforced by ``tests/unit/test_architecture.py``).
"""

from functools import lru_cache

import redis
from sqlalchemy.orm import Session, sessionmaker

from explorai_backend.config import get_settings
from explorai_db.blobs import BlobStore, LocalBlobStore
from explorai_db.session import make_engine, make_session_factory


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    engine = make_engine(str(get_settings().database_url))
    return make_session_factory(engine)


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(str(get_settings().redis_url))


@lru_cache
def get_blob_store() -> BlobStore:
    return LocalBlobStore(get_settings().blob_dir)
