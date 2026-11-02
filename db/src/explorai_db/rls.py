"""Identity-scoped sessions for Row-Level Security.

Migration 0003 enables RLS on ``documents``, ``raw_artifacts``,
``document_versions`` and ``sections`` (0005 adds ``chunks`` and
``chunk_index_entries``, read-only) with policies for the ``explorai_app``
role, keyed on ``app_user()`` (the ``app.user_id`` setting). The table owner
bypasses them (RLS is enabled without ``FORCE``) so the pipeline can ingest for
everyone; the API must therefore never query as the owner, only through
:func:`scoped_session`. The backend receives a :class:`ScopedSessions` instead
of the factory itself, so it has no unscoped way to open a session.

Both the role switch and the identity are transaction-local (``SET LOCAL`` and
``set_config(..., true)``): they end with the commit or rollback, so a pooled
connection can never carry them over to the next request.
"""

import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

APP_ROLE = "explorai_app"

# Lets callers type the factory they pass in without importing ``sessionmaker``,
# which the backend's architecture test reserves for the module that builds it.
type SessionFactory = sessionmaker[Session]

# SET takes no bind parameters; the role is a constant, never user input.
_SET_ROLE = text(f"SET LOCAL ROLE {APP_ROLE}")
_SET_IDENTITY = text("SELECT set_config('app.user_id', :uid, true)")


@contextmanager
def scoped_session(
    factory: SessionFactory, user_id: uuid.UUID | None
) -> Iterator[Session]:
    """Yield a session in one transaction run as ``explorai_app`` for ``user_id``.

    Commits when the block succeeds and rolls back when it raises. With
    ``user_id=None`` the identity is empty, ``app_user()`` is ``NULL`` and only
    the public corpus is visible.
    """
    with factory.begin() as session:
        session.execute(_SET_ROLE)
        session.execute(_SET_IDENTITY, {"uid": "" if user_id is None else str(user_id)})
        yield session


class ScopedSessions:
    """The only way the API opens a session: always through :func:`scoped_session`.

    Holding the raw factory would let a repository write ``self._factory()`` and
    query as the table owner, past every RLS policy. This wrapper keeps the
    factory in a name-mangled slot and offers one method, so the unscoped path is
    not reachable by accident (Python cannot make it impossible on purpose).
    """

    __slots__ = ("__factory",)

    def __init__(self, factory: SessionFactory) -> None:
        self.__factory = factory

    def scoped(self, user_id: uuid.UUID | None) -> AbstractContextManager[Session]:
        """A transaction run as ``explorai_app`` for ``user_id`` (see above)."""
        return scoped_session(self.__factory, user_id)
