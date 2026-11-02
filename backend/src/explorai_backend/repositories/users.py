"""Accounts. ``users`` is not under RLS: login looks an email up before anyone
is known, so these queries run with no identity (``sessions.scoped(None)``)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from explorai_db.models import User
from explorai_db.rls import ScopedSessions

EMAIL_INDEX = "ix_users_email_lower"


class EmailTaken(Exception):
    """Another account already uses this email (compared case-insensitively)."""


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: uuid.UUID
    email: str
    password_hash: str
    is_active: bool

    def __repr__(self) -> str:
        # Keep the hash out of any accidental log line or traceback.
        return f"UserRecord(id={self.id!s}, email={self.email!r}, ...)"


def _record(user: User) -> UserRecord:
    return UserRecord(user.id, user.email, user.password_hash, user.is_active)


def _violates_email_index(error: IntegrityError) -> bool:
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == EMAIL_INDEX


class UsersRepository:
    def __init__(self, sessions: ScopedSessions) -> None:
        self._sessions = sessions

    def create(self, email: str, password_hash: str) -> UserRecord:
        try:
            with self._sessions.scoped(None) as session:
                user = User(email=email.lower(), password_hash=password_hash)
                session.add(user)
                session.flush()
                return _record(user)
        except IntegrityError as error:
            if _violates_email_index(error):
                # "from None": the driver error carries the statement parameters.
                raise EmailTaken from None
            raise

    def by_email(self, email: str) -> UserRecord | None:
        with self._sessions.scoped(None) as session:
            user = session.scalars(
                select(User).where(func.lower(User.email) == email.lower())
            ).one_or_none()
            return None if user is None else _record(user)

    def by_id(self, user_id: uuid.UUID) -> UserRecord | None:
        with self._sessions.scoped(None) as session:
            user = session.get(User, user_id)
            return None if user is None else _record(user)
