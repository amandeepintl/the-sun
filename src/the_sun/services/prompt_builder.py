"""Prompt assembly.

Turns stored turns, saved memories and a task instruction into the message list a
provider receives. The provider's context budget is enforced by the gateway, so
this module only decides *what* belongs in the prompt, never what to say.
"""

from __future__ import annotations

from collections.abc import Sequence

from the_sun.ai import AIRole, ChatMessage
from the_sun.db.models import Memory, Message, MessageRole
from the_sun.services.base import ServiceContext
from the_sun.services.prompts import memory_section

__all__ = ["PromptBuilder"]


class PromptBuilder:
    """Builds provider messages from real database rows and a task instruction."""

    def __init__(self, context: ServiceContext) -> None:
        self._context = context

    @property
    def default_system_prompt(self) -> str:
        """The deployment's standing instructions, from configuration."""
        return self._context.settings.ai_system_prompt

    def system_message(
        self, *, guild_prompt: str | None, memories: Sequence[Memory] = ()
    ) -> ChatMessage:
        """The system message: configured instructions plus saved facts."""
        instructions = (guild_prompt or "").strip() or self.default_system_prompt
        sections = [instructions]
        if memories:
            sections.append(memory_section([(entry.key, entry.value) for entry in memories]))
        return ChatMessage.system("\n\n".join(sections))

    def history_messages(self, rows: Sequence[Message]) -> list[ChatMessage]:
        """Stored turns, oldest first, in the providers' own role vocabulary."""
        messages: list[ChatMessage] = []
        for row in rows:
            content = row.content.strip()
            if not content:
                continue
            if row.role is MessageRole.ASSISTANT:
                messages.append(ChatMessage.assistant(content))
            elif row.role is MessageRole.USER:
                messages.append(ChatMessage.user(content))
        return messages

    def assemble(
        self,
        *,
        guild_prompt: str | None,
        memories: Sequence[Memory],
        history: Sequence[Message],
        instruction: str,
    ) -> list[ChatMessage]:
        """Full request: instructions, conversation so far, then the new task."""
        messages = [self.system_message(guild_prompt=guild_prompt, memories=memories)]
        messages.extend(self.history_messages(history))
        messages.append(ChatMessage(role=AIRole.USER, content=instruction.strip()))
        return messages
