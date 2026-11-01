"""Login limits under concurrency, against real PostgreSQL and Redis."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import redis
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from explorai_backend.auth.limits import FixedWindowLimiter
from explorai_backend.auth.sessions import SessionStore
from explorai_backend.repositories.users import UsersRepository
from explorai_backend.services.auth import (
    AuthService,
    InvalidCredentials,
    TooManyAttempts,
)

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
EMAIL = "alice@example.org"
ATTEMPTS = 20


@pytest.fixture
def service(
    session_factory: sessionmaker[Session], redis_client: redis.Redis
) -> AuthService:
    with session_factory.begin() as session:
        session.execute(text("TRUNCATE users CASCADE"))
    return AuthService(
        UsersRepository(session_factory),
        SessionStore(redis_client, idle_seconds=86_400, max_seconds=604_800),
        FixedWindowLimiter(redis_client, "login:email", limit=5, window_seconds=900),
        FixedWindowLimiter(redis_client, "login:ip", limit=20, window_seconds=900),
    )


def _concurrent_outcomes(
    service: AuthService, attempts: list[tuple[str, str]]
) -> list[str]:
    """Run wrong-password logins ``(email, ip)`` all at once, in threads."""
    barrier = threading.Barrier(len(attempts))

    def attempt(email_and_ip: tuple[str, str]) -> str:
        email, ip = email_and_ip
        barrier.wait()  # release every attempt at the same moment
        try:
            service.login(email, PASSWORD + "!", ip)
        except InvalidCredentials:
            return "invalid"
        except TooManyAttempts:
            return "limited"
        return "logged in"

    with ThreadPoolExecutor(max_workers=len(attempts)) as pool:
        return list(pool.map(attempt, attempts))


def test_concurrent_logins_cannot_exceed_the_email_limit(
    service: AuthService,
) -> None:
    service.signup(EMAIL, PASSWORD)

    outcomes = _concurrent_outcomes(
        service, [(EMAIL, f"10.0.0.{n}") for n in range(ATTEMPTS)]
    )

    assert outcomes.count("invalid") == 5
    assert outcomes.count("limited") == ATTEMPTS - 5


def test_concurrent_logins_cannot_exceed_the_ip_limit(
    service: AuthService, redis_client: redis.Redis
) -> None:
    # Spread over emails so only the IP limit (20) applies.
    ip_limiter = FixedWindowLimiter(redis_client, "login:ip", 20, 900)
    for _ in range(15):
        ip_limiter.hit("203.0.113.7")

    outcomes = _concurrent_outcomes(
        service, [(f"user{n}@example.org", "203.0.113.7") for n in range(10)]
    )

    assert outcomes.count("invalid") == 5
    assert outcomes.count("limited") == 5
