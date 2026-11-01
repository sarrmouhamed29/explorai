import base64
import uuid
from datetime import UTC, datetime

import pytest

from explorai_backend.api.pagination import (
    decode_cursor,
    decode_ordinal_cursor,
    encode_cursor,
    encode_ordinal_cursor,
)


def test_cursor_round_trip() -> None:
    created_at = datetime(2026, 11, 1, 12, 30, 5, 123456, tzinfo=UTC)
    item_id = uuid.uuid4()
    assert decode_cursor(encode_cursor(created_at, item_id)) == (created_at, item_id)


def test_ordinal_cursor_round_trip() -> None:
    assert decode_ordinal_cursor(encode_ordinal_cursor(42)) == 42


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "not a cursor",
        "!!!",
        "e30",  # {} : valid JSON, wrong shape
        _b64("[1, 2]"),
        _b64('{"t": "2026-11-01T00:00:00", "i": "' + str(uuid.uuid4()) + '"}'),
        _b64('{"t": "2026-11-01T00:00:00+00:00", "i": "nope"}'),
        _b64('{"t": "nope", "i": "' + str(uuid.uuid4()) + '"}'),
        "é",
    ],
)
def test_garbage_cursor_is_rejected(garbage: str) -> None:
    with pytest.raises(ValueError):
        decode_cursor(garbage)


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "e30",
        _b64('{"o": "3"}'),
        _b64('{"o": true}'),
        _b64('{"o": -1}'),
        _b64('{"o": 2147483648}'),
        _b64('{"o": 1000000000000000000000000000000}'),
    ],
)
def test_garbage_ordinal_cursor_is_rejected(garbage: str) -> None:
    with pytest.raises(ValueError):
        decode_ordinal_cursor(garbage)


def test_ordinal_bounds_are_accepted() -> None:
    assert decode_ordinal_cursor(encode_ordinal_cursor(0)) == 0
    assert decode_ordinal_cursor(encode_ordinal_cursor(2**31 - 1)) == 2**31 - 1
