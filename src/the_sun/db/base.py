"""Declarative base and shared column types.

The schema targets PostgreSQL (JSONB, native UUID, timezone-aware timestamps)
but keeps portable fallbacks so the same models describe themselves accurately
in any engine. Migrations are the only other place allowed to contain schema
declarations.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import JSON, Text, Uuid

__all__ = ["NAMING_CONVENTION", "Base", "DiscordSnowflake", "JsonColumn"]

#: Deterministic constraint names keep Alembic migrations diffable.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: Discord identifiers are snowflakes: 64-bit integers assigned by Discord.
DiscordSnowflake = BigInteger

#: JSONB on PostgreSQL, generic JSON elsewhere.
JsonColumn = JSON().with_variant(JSONB(astext_type=Text()), "postgresql")


class Base(DeclarativeBase):
    """Base class for every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # SQLAlchemy reads this mapping at class-creation time and expects a mutable
    # class attribute, which is why it is not annotated as a ClassVar.
    type_annotation_map = {  # noqa: RUF012
        datetime: DateTime(timezone=True),
        dict[str, Any]: JsonColumn,
        list[str]: JsonColumn,
        list[int]: JsonColumn,
        Any: JsonColumn,
        uuid.UUID: Uuid(as_uuid=True, native_uuid=True),
    }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        identifier = getattr(self, "id", None) or getattr(self, "guild_id", None)
        return f"<{type(self).__name__} {identifier}>"
