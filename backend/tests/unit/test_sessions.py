import hashlib
import uuid

import fakeredis
import pytest
from fakes import FakeClock, install_clock, text

from explorai_backend.auth.sessions import SessionStore

IDLE = 86_400
MAX = 604_800


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    return install_clock(monkeypatch)


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


@pytest.fixture
def store(redis: fakeredis.FakeRedis, clock: FakeClock) -> SessionStore:
    return SessionStore(redis, idle_seconds=IDLE, max_seconds=MAX, clock=clock)


def test_create_and_resolve(store: SessionStore) -> None:
    user_id = uuid.uuid4()

    token = store.create(user_id)

    assert store.resolve(token) == user_id


def test_tokens_are_random_and_256_bits(store: SessionStore) -> None:
    user_id = uuid.uuid4()

    tokens = {store.create(user_id) for _ in range(5)}

    assert len(tokens) == 5
    # token_urlsafe(32): 32 random bytes, base64url without padding = 43 chars.
    assert all(len(token) == 43 for token in tokens)


def test_session_token_is_stored_hashed(
    store: SessionStore, redis: fakeredis.FakeRedis
) -> None:
    token = store.create(uuid.uuid4())

    keys = [text(key) for key in redis.keys("*")]
    assert keys == [f"session:{hashlib.sha256(token.encode()).hexdigest()}"]
    stored_values = "".join(text(v) for v in redis.hgetall(keys[0]).values())
    assert token not in stored_values


def test_unknown_token_resolves_to_none(store: SessionStore) -> None:
    assert store.resolve("not-a-session") is None


def test_idle_expiry(store: SessionStore, clock: FakeClock) -> None:
    token = store.create(uuid.uuid4())

    clock.advance(IDLE + 1)

    assert store.resolve(token) is None


def test_use_refreshes_the_idle_timeout(store: SessionStore, clock: FakeClock) -> None:
    user_id = uuid.uuid4()
    token = store.create(user_id)

    for _ in range(3):
        clock.advance(IDLE - 60)
        assert store.resolve(token) == user_id


def test_absolute_expiry_beats_idle_refresh(
    store: SessionStore, clock: FakeClock, redis: fakeredis.FakeRedis
) -> None:
    """Review Focus 2: an active session still dies 7 days after login."""
    user_id = uuid.uuid4()
    token = store.create(user_id)
    key = f"session:{hashlib.sha256(token.encode()).hexdigest()}"

    # Use the session every 12 hours: the idle TTL never runs out.
    while clock.now + IDLE / 2 < 1_000_000.0 + MAX:
        clock.advance(IDLE / 2)
        assert store.resolve(token) == user_id
        # The refreshed TTL never reaches past the absolute deadline.
        assert redis.ttl(key) <= 1_000_000.0 + MAX - clock.now + 1

    clock.advance(IDLE / 2)  # now past 7 days, idle TTL possibly still running
    assert store.resolve(token) is None


def test_absolute_expiry_rejects_a_key_still_alive_in_redis(
    store: SessionStore, clock: FakeClock, redis: fakeredis.FakeRedis
) -> None:
    token = store.create(uuid.uuid4())
    key = f"session:{hashlib.sha256(token.encode()).hexdigest()}"
    redis.persist(key)  # simulate a key whose TTL did not cap it

    clock.advance(MAX)

    assert store.resolve(token) is None
    assert not redis.exists(key)


def test_revoke(store: SessionStore) -> None:
    user_id = uuid.uuid4()
    token = store.create(user_id)
    other = store.create(user_id)

    store.revoke(token)
    store.revoke(token)  # revoking twice is not an error

    assert store.resolve(token) is None
    assert store.resolve(other) == user_id


def test_corrupt_session_record_resolves_to_none(
    store: SessionStore, redis: fakeredis.FakeRedis
) -> None:
    token = store.create(uuid.uuid4())
    key = f"session:{hashlib.sha256(token.encode()).hexdigest()}"
    redis.hset(key, "user_id", "not-a-uuid")

    assert store.resolve(token) is None
