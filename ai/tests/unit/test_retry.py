import pytest

from explorai_ai.errors import ProviderError, ProviderRateLimited, ProviderUnavailable
from explorai_ai.retry import retry_transient


class Flaky:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return "ok"


def test_retries_unavailable_then_succeeds() -> None:
    fn = Flaky([ProviderUnavailable("a"), ProviderRateLimited("b")])
    assert retry_transient(fn, sleep=lambda _: None) == "ok"
    assert fn.calls == 3


def test_does_not_retry_other_errors() -> None:
    fn = Flaky([ProviderError("bad request")])
    with pytest.raises(ProviderError):
        retry_transient(fn, sleep=lambda _: None)
    assert fn.calls == 1

    other = Flaky([ValueError("boom")])
    with pytest.raises(ValueError, match="boom"):
        retry_transient(other, sleep=lambda _: None)
    assert other.calls == 1


def test_gives_up_after_attempts_and_raises_last() -> None:
    fn = Flaky(
        [ProviderUnavailable("1"), ProviderUnavailable("2"), ProviderUnavailable("3")]
    )
    with pytest.raises(ProviderUnavailable, match="3"):
        retry_transient(fn, attempts=3, sleep=lambda _: None)
    assert fn.calls == 3


def test_sleeps_between_attempts() -> None:
    slept: list[float] = []
    fn = Flaky([ProviderUnavailable("1"), ProviderUnavailable("2")])
    retry_transient(fn, delays=(0.5, 2.0), sleep=slept.append)
    assert slept == [0.5, 2.0]


def test_last_delay_is_reused_when_delays_run_out() -> None:
    slept: list[float] = []
    fn = Flaky([ProviderUnavailable("x")] * 3)
    retry_transient(fn, attempts=4, delays=(1.0,), sleep=slept.append)
    assert slept == [1.0, 1.0, 1.0]
