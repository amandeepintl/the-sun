"""Cursor pagination.

Keyset pagination keeps listings stable while new rows are being written, which
matters for message history and usage logs. Cursors are opaque base64 strings
carrying a timestamp and a row identifier.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime

from the_sun.errors import InvalidInputError

__all__ = ["Cursor", "Page", "decode_cursor", "encode_cursor"]

_SEPARATOR = "|"


@dataclass(frozen=True, slots=True)
class Cursor:
    """Decoded position in a keyset-ordered listing."""

    created_at: datetime
    identifier: str


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of results plus the cursor for the next page, if any."""

    items: list[T]
    next_cursor: str | None
    limit: int

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


def encode_cursor(created_at: datetime, identifier: object) -> str:
    """Encode a keyset position. ``identifier`` may be a uuid or an integer."""
    timestamp = created_at.astimezone(UTC).isoformat()
    raw = f"{timestamp}{_SEPARATOR}{identifier}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> Cursor:
    """Decode a cursor produced by :func:`encode_cursor`."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidInputError("the pagination cursor is not valid") from exc
    timestamp, separator, identifier = raw.partition(_SEPARATOR)
    if not separator or not identifier:
        raise InvalidInputError("the pagination cursor is not valid")
    try:
        created_at = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise InvalidInputError("the pagination cursor is not valid") from exc
    return Cursor(created_at=created_at, identifier=identifier)
