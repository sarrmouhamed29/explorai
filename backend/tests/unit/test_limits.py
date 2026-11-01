import fakeredis
import pytest
from fakes import FakeClock, install_clock, text

from explorai_backend.auth.limits import FixedWindowLimiter


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    return install_clock(monkeypatch)


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


@pytest.fixture
def limiter(redis: fakeredis.FakeRedis) -> FixedWindowLimiter:
    return FixedWindowLimiter(redis, prefix="test", limit=3, window_seconds=900)


def test_limiter_window(limiter: FixedWindowLimiter, clock: FakeClock) -> None:
    assert [limiter.hit("k") for _ in range(3)] == [1, 2, 3]
    assert limiter.exceeded("k")

    clock.advance(899)
    assert limiter.exceeded("k")  # later hits do not extend the window
    clock.advance(2)
    assert not limiter.exceeded("k")
    assert limiter.hit("k") == 1


def test_hits_do_not_extend_the_window(
    limiter: FixedWindowLimiter, clock: FakeClock
) -> None:
    limiter.hit("k")
    clock.advance(800)
    limiter.hit("k")
    clock.advance(101)

    assert limiter.hit("k") == 1


def test_below_the_limit_is_not_exceeded(limiter: FixedWindowLimiter) -> None:
    assert not limiter.exceeded("k")
    limiter.hit("k")
    limiter.hit("k")

    assert not limiter.exceeded("k")


def test_keys_are_independent_and_prefixed(
    limiter: FixedWindowLimiter, redis: fakeredis.FakeRedis
) -> None:
    for _ in range(3):
        limiter.hit("a")

    assert limiter.exceeded("a")
    assert not limiter.exceeded("b")
    assert [text(key) for key in redis.keys("*")] == ["test:a"]


def test_reset(limiter: FixedWindowLimiter) -> None:
    for _ in range(3):
        limiter.hit("k")

    limiter.reset("k")

    assert not limiter.exceeded("k")
