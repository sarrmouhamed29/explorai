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
        # Checked before any hashing: a refused attempt costs no Argon2 work, and
        # it is refused even with the right password, so guessing cannot go on.
        if self._email_limiter.exceeded(email) or self._ip_limiter.exceeded(ip):
            raise TooManyAttempts
        user = self._users.by_email(email)
        if user is None:
            passwords.verify_dummy(password)
            ok = False
        else:
            ok = passwords.verify_password(user.password_hash, password)
            ok = ok and user.is_active
        if user is None or not ok:
            self._email_limiter.hit(email)
            self._ip_limiter.hit(ip)
            raise InvalidCredentials
        self._email_limiter.reset(email)
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
