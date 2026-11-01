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


def test_limit_is_exposed(limiter: FixedWindowLimiter) -> None:
    assert limiter.limit == 3


def test_release_gives_back_one_event_and_keeps_the_window(
    limiter: FixedWindowLimiter, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    limiter.hit("k")
    clock.advance(100)
    limiter.hit("k")

    limiter.release("k")

    assert redis.get("test:k") == b"1"
    assert redis.ttl("test:k") == 800  # the window still ends where it began


def test_release_never_creates_or_lowers_below_zero(
    limiter: FixedWindowLimiter, redis: fakeredis.FakeRedis
) -> None:
    limiter.release("missing")
    redis.set("test:zero", 0)
    limiter.release("zero")

    assert not redis.exists("test:missing")
    assert redis.get("test:zero") == b"0"


def test_release_is_dropped_on_a_concurrent_change(
    limiter: FixedWindowLimiter,
    redis: fakeredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter.hit("k")
    limiter.hit("k")
    real_pipeline = redis.pipeline

    def racing_pipeline(*args: object, **kwargs: object) -> object:
        pipe = real_pipeline()
        original_get = pipe.get

        def get(name: str) -> object:
            value = original_get(name)
            redis.incr(name)  # another request counts in between
            return value

        monkeypatch.setattr(pipe, "get", get)
        return pipe

    monkeypatch.setattr(redis, "pipeline", racing_pipeline)
    limiter.release("k")

    # The release lost the race and was dropped: strict, never lenient.
    assert redis.get("test:k") == b"3"
