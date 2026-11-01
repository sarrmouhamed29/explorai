import uuid

import fakeredis
import pytest
from fakes import FakeClock, install_clock

from explorai_backend.auth import passwords
from explorai_backend.auth.limits import FixedWindowLimiter
from explorai_backend.auth.sessions import SessionStore
from explorai_backend.repositories.users import EmailTaken, UserRecord
from explorai_backend.services.auth import (
    AuthService,
    InvalidCredentials,
    PublicUser,
    TooManyAttempts,
)

PASSWORD = "correct horse battery staple"
IP = "203.0.113.7"


class InMemoryUsers:
    """Same contract as ``UsersRepository``, without a database."""

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, UserRecord] = {}

    def create(self, email: str, password_hash: str) -> UserRecord:
        if self.by_email(email) is not None:
            raise EmailTaken
        record = UserRecord(uuid.uuid4(), email.lower(), password_hash, True)
        self.rows[record.id] = record
        return record

    def by_email(self, email: str) -> UserRecord | None:
        return next(
            (r for r in self.rows.values() if r.email == email.lower()),
            None,
        )

    def by_id(self, user_id: uuid.UUID) -> UserRecord | None:
        return self.rows.get(user_id)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    return install_clock(monkeypatch)


@pytest.fixture
def users() -> InMemoryUsers:
    return InMemoryUsers()


@pytest.fixture
def service(users: InMemoryUsers, clock: FakeClock) -> AuthService:
    redis = fakeredis.FakeRedis()
    return AuthService(
        users,
        SessionStore(redis, idle_seconds=86_400, max_seconds=604_800, clock=clock),
        FixedWindowLimiter(redis, "login:email", limit=5, window_seconds=900),
        FixedWindowLimiter(redis, "login:ip", limit=20, window_seconds=900),
    )


def test_signup_returns_the_public_user_and_hashes_the_password(
    service: AuthService, users: InMemoryUsers
) -> None:
    user = service.signup("Alice@Example.org", PASSWORD)

    assert user == PublicUser(id=user.id, email="alice@example.org")
    stored = users.rows[user.id].password_hash
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


def test_signup_duplicate_email_raises(service: AuthService) -> None:
    service.signup("alice@example.org", PASSWORD)

    with pytest.raises(EmailTaken):
        service.signup("ALICE@example.org", PASSWORD)


def test_login_logout_and_user_for_token(service: AuthService) -> None:
    user = service.signup("alice@example.org", PASSWORD)

    token = service.login("alice@example.org", PASSWORD, IP)

    assert service.user_for_token(token) == user
    service.logout(token)
    assert service.user_for_token(token) is None


def test_email_is_case_insensitive(service: AuthService) -> None:
    user = service.signup("Alice@Example.org", PASSWORD)

    token = service.login("ALICE@EXAMPLE.ORG", PASSWORD, IP)

    assert service.user_for_token(token) == user


def test_wrong_password_is_invalid_credentials(service: AuthService) -> None:
    service.signup("alice@example.org", PASSWORD)

    with pytest.raises(InvalidCredentials):
        service.login("alice@example.org", "wrong password!!", IP)


def test_login_unknown_email_still_verifies_a_hash(
    service: AuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = passwords.verify_dummy

    def spy(plain: str) -> None:
        calls.append(plain)
        real(plain)

    monkeypatch.setattr(passwords, "verify_dummy", spy)

    with pytest.raises(InvalidCredentials):
        service.login("nobody@example.org", PASSWORD, IP)

    assert calls == [PASSWORD]


def test_inactive_user_cannot_log_in(
    service: AuthService, users: InMemoryUsers
) -> None:
    user = service.signup("alice@example.org", PASSWORD)
    token = service.login("alice@example.org", PASSWORD, IP)
    record = users.rows[user.id]
    users.rows[user.id] = UserRecord(
        record.id, record.email, record.password_hash, False
    )

    with pytest.raises(InvalidCredentials):
        service.login("alice@example.org", PASSWORD, IP)
    assert service.user_for_token(token) is None


def test_session_of_a_deleted_user_resolves_to_none(
    service: AuthService, users: InMemoryUsers
) -> None:
    user = service.signup("alice@example.org", PASSWORD)
    token = service.login("alice@example.org", PASSWORD, IP)
    del users.rows[user.id]

    assert service.user_for_token(token) is None


def test_login_is_limited_per_email_and_per_ip(service: AuthService) -> None:
    service.signup("alice@example.org", PASSWORD)

    # Per email: 5 failures, then even the right password is refused.
    for n in range(5):
        with pytest.raises(InvalidCredentials):
            service.login("Alice@example.org", "wrong password!!", f"10.0.0.{n}")
    with pytest.raises(TooManyAttempts):
        service.login("alice@example.org", PASSWORD, "10.0.1.1")

    # Per IP: 20 failures over many emails, then any login from that IP is refused.
    for n in range(20):
        with pytest.raises(InvalidCredentials):
            service.login(f"user{n}@example.org", PASSWORD, IP)
    with pytest.raises(TooManyAttempts):
        service.login("fresh@example.org", PASSWORD, IP)


def test_limited_attempts_do_not_verify_a_hash(
    service: AuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in range(5):
        with pytest.raises(InvalidCredentials):
            service.login("nobody@example.org", PASSWORD, IP)

    def fail(plain: str) -> None:
        raise AssertionError("a refused attempt must not spend a hash")

    monkeypatch.setattr(passwords, "verify_dummy", fail)
    with pytest.raises(TooManyAttempts):
        service.login("nobody@example.org", PASSWORD, IP)


def test_successful_login_resets_the_email_counter(service: AuthService) -> None:
    service.signup("alice@example.org", PASSWORD)

    for _ in range(4):
        with pytest.raises(InvalidCredentials):
            service.login("alice@example.org", "wrong password!!", IP)
    service.login("alice@example.org", PASSWORD, IP)

    for _ in range(4):
        with pytest.raises(InvalidCredentials):
            service.login("alice@example.org", "wrong password!!", IP)
    assert service.login("alice@example.org", PASSWORD, IP)


def test_each_login_issues_a_new_token(service: AuthService) -> None:
    service.signup("alice@example.org", PASSWORD)

    first = service.login("alice@example.org", PASSWORD, IP)
    second = service.login("alice@example.org", PASSWORD, IP)

    assert first != second


def test_successful_logins_do_not_count_against_the_ip(service: AuthService) -> None:
    service.signup("alice@example.org", PASSWORD)

    for _ in range(25):  # more than the IP limit of 20
        assert service.login("alice@example.org", PASSWORD, IP)


def test_refused_attempts_are_not_counted_as_failures(service: AuthService) -> None:
    service.signup("alice@example.org", PASSWORD)
    for _ in range(5):
        with pytest.raises(InvalidCredentials):
            service.login("alice@example.org", "wrong password!!", IP)

    # Refused attempts on the locked email must not use up the IP's budget:
    # 15 more failures on other emails are still allowed from this address.
    for _ in range(10):
        with pytest.raises(TooManyAttempts):
            service.login("alice@example.org", PASSWORD, IP)
    for n in range(15):
        with pytest.raises(InvalidCredentials):
            service.login(f"user{n}@example.org", PASSWORD, IP)
    with pytest.raises(TooManyAttempts):
        service.login("fresh@example.org", PASSWORD, IP)
