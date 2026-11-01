"""Test doubles shared by the unit tests."""

import time

import pytest


def text(value: bytes | str) -> str:
    """A Redis reply as text, whatever the client's decode_responses."""
    return value.decode() if isinstance(value, bytes) else value


class FakeClock:
    """A controllable ``time.time`` replacement, in seconds."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def install_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Drive both the code under test and fakeredis's TTLs from one fake clock.

    fakeredis reads ``time.time()`` on every command to expire keys, so patching
    the module attribute makes key expiry follow the fake clock too.
    """
    clock = FakeClock()
    monkeypatch.setattr(time, "time", clock)
    return clock
