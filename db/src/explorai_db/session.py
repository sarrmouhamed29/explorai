"""Engine and session factory helpers."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str) -> Engine:
    """Create a synchronous engine on the psycopg 3 driver.

    ``postgresql://`` URLs (the form used across the project) are rewritten
    to ``postgresql+psycopg://``; already qualified URLs are left alone.
    """
    url = make_url(database_url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
