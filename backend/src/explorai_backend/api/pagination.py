"""Opaque keyset cursors.

A cursor carries the sort key of the last item seen, so the next page starts
right after it. Unlike an offset it stays correct when rows are added meanwhile
and the database can seek straight to it through the index. Clients treat it as
an opaque string; the base64 JSON form is an implementation detail.
"""

import base64
import binascii
import json
import uuid
from datetime import datetime


def _pack(payload: dict[str, str | int]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unpack(cursor: str) -> dict[str, object]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, binascii.Error):
        raise ValueError("invalid cursor") from None
    if not isinstance(payload, dict):
        raise ValueError("invalid cursor")
    return payload


def encode_cursor(created_at: datetime, id: uuid.UUID) -> str:
    return _pack({"t": created_at.isoformat(), "i": str(id)})


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Return ``(created_at, id)``; raise ``ValueError`` on anything else."""
    payload = _unpack(cursor)
    try:
        created_at = datetime.fromisoformat(str(payload["t"]))
        item_id = uuid.UUID(str(payload["i"]))
    except (KeyError, ValueError):
        raise ValueError("invalid cursor") from None
    if created_at.tzinfo is None:
        raise ValueError("invalid cursor")
    return created_at, item_id


def encode_ordinal_cursor(ordinal: int) -> str:
    return _pack({"o": ordinal})


def decode_ordinal_cursor(cursor: str) -> int:
    ordinal = _unpack(cursor).get("o")
    if not isinstance(ordinal, int) or isinstance(ordinal, bool):
        raise ValueError("invalid cursor")
    return ordinal
