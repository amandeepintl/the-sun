"""Explicit memory.

Memory is opt-in and user-driven: entries exist because a member asked for them
with ``/memory save``, and they are stored with the user's own words. They are
scoped to the guild where they were saved, or to the member globally, and they are
only injected into a prompt when the guild has memory enabled and the member asked
inside that guild.
"""

from __future__ import annotations

import re
import unicodedata

from the_sun.db.models import Memory, MemorySource
from the_sun.errors import InvalidInputError
from the_sun.services.base import ServiceContext

__all__ = ["KEY_PATTERN", "MemoryService", "normalise_key"]

#: Keys are stable, readable identifiers: lowercase, digits, dashes, underscores.
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_KEY_CLEANUP = re.compile(r"[^a-z0-9_-]+")


def normalise_key(raw: str) -> str:
    """Normalise a user-supplied key and reject anything unusable."""
    candidate = unicodedata.normalize("NFKD", raw.strip().lower())
    candidate = _KEY_CLEANUP.sub("_", candidate.replace(" ", "_")).strip("_-")
    if not candidate:
        raise InvalidInputError(
            "a memory key is required",
            public_message="A memory needs a short key, for example `timezone`.",
        )
    if not KEY_PATTERN.match(candidate):
        raise InvalidInputError(
            f"memory key {candidate!r} is not usable",
            public_message=(
                "That key cannot be used. Use letters, digits, dashes or underscores, "
                "up to 64 characters."
            ),
        )
    return candidate


class MemoryService:
    """Reads and writes the member's explicitly saved memories."""

    def __init__(self, context: ServiceContext) -> None:
        self._context = context

    @property
    def max_entries(self) -> int:
        return self._context.settings.memory_max_entries

    @property
    def max_value_chars(self) -> int:
        return self._context.settings.memory_max_value_chars

    def validate_value(self, raw: str) -> str:
        """Trim a value and enforce the configured length cap."""
        value = " ".join(raw.split())
        if not value:
            raise InvalidInputError(
                "a memory value is required",
                public_message="A memory needs something to remember.",
            )
        if len(value) > self.max_value_chars:
            raise InvalidInputError(
                f"memory value of {len(value)} characters exceeds {self.max_value_chars}",
                public_message=(
                    f"That memory is too long. Keep it under {self.max_value_chars} characters."
                ),
            )
        return value

    async def list_for_user(self, user_id: int, *, guild_id: int | None) -> list[Memory]:
        """Memories visible to this member in this context."""
        async with self._context.unit_of_work() as uow:
            return await uow.memories.list_for_user(user_id, guild_id=guild_id)

    async def save(self, *, user_id: int, guild_id: int | None, key: str, value: str) -> Memory:
        """Store one memory, refusing to exceed the configured entry cap."""
        normalised = normalise_key(key)
        cleaned = self.validate_value(value)
        async with self._context.unit_of_work() as uow:
            existing = await uow.memories.get(user_id=user_id, key=normalised, guild_id=guild_id)
            if existing is None:
                current = await uow.memories.list_for_user(user_id, guild_id=guild_id)
                if len(current) >= self.max_entries:
                    raise InvalidInputError(
                        f"user {user_id} already has {len(current)} memories",
                        public_message=(
                            f"You already have {self.max_entries} saved memories, which is the "
                            "limit. Delete one with `/memory delete` and try again."
                        ),
                    )
            return await uow.memories.upsert(
                user_id=user_id,
                key=normalised,
                value=cleaned,
                guild_id=guild_id,
                source=MemorySource.EXPLICIT,
            )

    async def delete(self, *, user_id: int, guild_id: int | None, key: str) -> bool:
        """Forget one memory. Returns whether it existed."""
        normalised = normalise_key(key)
        async with self._context.unit_of_work() as uow:
            if guild_id is not None:
                # A member may see global entries inside a guild; delete the scoped
                # one when it exists, and otherwise the global one.
                scoped = await uow.memories.delete(
                    user_id=user_id, key=normalised, guild_id=guild_id
                )
                if scoped:
                    return True
            return await uow.memories.delete(user_id=user_id, key=normalised, guild_id=None)

    async def clear(self, *, user_id: int, guild_id: int | None, include_global: bool) -> int:
        """Forget memories this member saved in this context.

        A direct message has only the global scope. Inside a guild the scoped
        entries are removed, together with the global ones when asked.
        """
        async with self._context.unit_of_work() as uow:
            if guild_id is None:
                return await uow.memories.delete_all_for_user(user_id)
            entries = await uow.memories.list_for_user(
                user_id, guild_id=guild_id, include_global=include_global
            )
            removed = 0
            for entry in entries:
                if await uow.memories.delete(
                    user_id=user_id, key=entry.key, guild_id=entry.guild_id
                ):
                    removed += 1
            return removed

    async def entries_for_prompt(
        self, *, user_id: int, guild_id: int | None, enabled: bool
    ) -> list[Memory]:
        """Memories to inject into a prompt, or nothing when disabled."""
        if not enabled:
            return []
        return await self.list_for_user(user_id, guild_id=guild_id)
