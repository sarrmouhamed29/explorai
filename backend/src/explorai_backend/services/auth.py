"""Sign-up, login and sessions."""

import uuid
from typing import Protocol

from pydantic import BaseModel

from explorai_backend.auth import passwords
from explorai_backend.auth.limits import FixedWindowLimiter
from explorai_backend.auth.sessions import SessionStore
from explorai_backend.repositories.users import UserRecord


class InvalidCredentials(Exception):
    """Unknown email, wrong password or inactive account: deliberately one error."""


class TooManyAttempts(Exception):
    """Too many recent login failures for this email or this IP address."""


class PublicUser(BaseModel):
    """What the API may reveal about an account: never the password hash."""

    id: uuid.UUID
    email: str


class Users(Protocol):
    def create(self, email: str, password_hash: str) -> UserRecord: ...
    def by_email(self, email: str) -> UserRecord | None: ...
    def by_id(self, user_id: uuid.UUID) -> UserRecord | None: ...


def _public(user: UserRecord) -> PublicUser:
    return PublicUser(id=user.id, email=user.email)


class AuthService:
    def __init__(
        self,
        users: Users,
        sessions: SessionStore,
        email_limiter: FixedWindowLimiter,
        ip_limiter: FixedWindowLimiter,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._email_limiter = email_limiter
        self._ip_limiter = ip_limiter

    def signup(self, email: str, password: str) -> PublicUser:
        """Create an account; raises ``EmailTaken``. Does not log in."""
        record = self._users.create(email.lower(), passwords.hash_password(password))
        return _public(record)

    def login(self, email: str, password: str, ip: str) -> str:
        """Return a new session token, or raise the same error for every failure."""
        email = email.lower()
        # Reserve the attempt before any hashing, with atomic increments: parallel
        # requests each get their own count, so a burst cannot all pass a check
        # made before any of them was counted. A refused attempt costs no Argon2
        # work, and it is refused even with the right password, so guessing
        # cannot go on.
        email_count = self._email_limiter.hit(email)
        ip_count = self._ip_limiter.hit(ip)
        if email_count > self._email_limiter.limit or ip_count > self._ip_limiter.limit:
            # Only failures count: give back what a refused attempt reserved.
            self._email_limiter.release(email)
            self._ip_limiter.release(ip)
            raise TooManyAttempts
        user = self._users.by_email(email)
        if user is None:
            passwords.verify_dummy(password)
            ok = False
        else:
            ok = passwords.verify_password(user.password_hash, password)
            ok = ok and user.is_active
        if user is None or not ok:
            raise InvalidCredentials  # the reserved attempts stay counted
        # A success clears the account's failures and does not count against
        # the address (several users may share one, behind NAT).
        self._email_limiter.reset(email)
        self._ip_limiter.release(ip)
        # A fresh random token on every login: a token planted before login
        # (session fixation) is never promoted to an authenticated session.
        return self._sessions.create(user.id)

    def logout(self, token: str) -> None:
        self._sessions.revoke(token)

    def user_for_token(self, token: str) -> PublicUser | None:
        user_id = self._sessions.resolve(token)
        if user_id is None:
            return None
        user = self._users.by_id(user_id)
        if user is None or not user.is_active:
            self._sessions.revoke(token)
            return None
        return _public(user)
