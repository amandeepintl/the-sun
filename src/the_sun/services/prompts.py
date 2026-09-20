"""Instruction templates.

These are the product's instructions to a language model. Every template is a
function of real input - a question, a quoted Discord message, a rendered
transcript - and none of them contains an answer, a summary or an example output.
The assistant's standing instructions live in
:mod:`the_sun.config.prompts` and are configuration.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "NO_TEXT_CONTENT",
    "TranscriptEntry",
    "answer_instruction",
    "explain_instruction",
    "memory_section",
    "message_instruction",
    "render_transcript",
    "summarize_instruction",
    "translate_instruction",
]

#: Marker used when a real Discord message carried no text (an embed, a sticker,
#: an attachment-only post). It describes the absence of content; it is not content.
NO_TEXT_CONTENT = "[no text content]"


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """One real Discord message, ready to be quoted to a model."""

    author: str
    content: str
    timestamp: datetime | None = None
    is_bot: bool = False
    has_attachments: bool = False


def _clip(text: str, limit: int) -> str:
    """Collapse runs of whitespace and clip to ``limit`` characters."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "\u2026"


def render_transcript(entries: Iterable[TranscriptEntry], *, max_chars_per_message: int) -> str:
    """Render messages in the order they were sent.

    Each line keeps the real author name and timestamp so the model can attribute
    statements correctly. Messages without text are marked rather than dropped, so
    the ordering stays faithful to the channel.
    """
    lines: list[str] = []
    for entry in entries:
        stamp = entry.timestamp.strftime("%Y-%m-%d %H:%M") if entry.timestamp else "unknown time"
        body = _clip(entry.content, max_chars_per_message) if entry.content.strip() else ""
        if entry.has_attachments:
            body = f"{body} [attachment]".strip()
        if not body:
            body = NO_TEXT_CONTENT
        author = f"{entry.author} (bot)" if entry.is_bot else entry.author
        lines.append(f"[{stamp}] {author}: {body}")
    return "\n".join(lines)


def answer_instruction(question: str) -> str:
    """A direct question, passed through unchanged.

    ``/ask`` sends what the member typed; adding framing around it would only make
    the prompt longer without adding information.
    """
    return question.strip()


def explain_instruction(subject: str, *, language: str | None = None) -> str:
    """Ask for an explanation of code or a concept."""
    qualifier = f"{language.strip()} " if language and language.strip() else ""
    return (
        f"Explain the following {qualifier}code or concept to someone who has not seen it "
        "before. Say what it does, how it works, and any pitfall or misconception worth "
        "knowing. Use a short fenced code block when that makes it clearer.\n\n"
        f"{subject.strip()}"
    )


def translate_instruction(
    text: str, *, target_language: str, source_language: str | None = None
) -> str:
    """Ask for a translation of text the member supplied."""
    target = target_language.strip()
    if source_language and source_language.strip():
        direction = f"from {source_language.strip()} into {target}"
    else:
        direction = f"into {target} (detect the source language yourself)"
    return (
        f"Translate the text below {direction}. Reply with the translation only, "
        "keeping the original tone and any code or formatting intact.\n\n"
        f"{text.strip()}"
    )


def summarize_instruction(
    transcript: str,
    *,
    message_count: int,
    channel_name: str | None = None,
    focus: str | None = None,
) -> str:
    """Ask for a summary of a transcript made of real Discord messages."""
    where = f"in #{channel_name}" if channel_name else "in a Discord channel"
    parts = [
        f"Summarise the conversation below, which is {message_count} consecutive messages "
        f"{where}, oldest first.",
        "Cover what was discussed, what was decided, and anything still open or worth "
        "acting on. Attribute positions to the people who took them. Do not invent "
        "participants, decisions or facts that are not in the transcript.",
    ]
    if focus and focus.strip():
        parts.append(f"The member asking is specifically interested in: {focus.strip()}")
    parts.append(f"--- start of transcript ---\n{transcript}\n--- end of transcript ---")
    return "\n\n".join(parts)


def message_instruction(
    content: str, *, author: str | None = None, request: str | None = None
) -> str:
    """Ask about one real message a member selected."""
    source = f" sent by {author}" if author else ""
    ask = (
        request.strip()
        if request and request.strip()
        else "Explain what it means and answer any question it contains."
    )
    return (
        f"A server member selected the following Discord message{source} and asked me to "
        "look at it.\n\n"
        f'"""\n{content.strip() or NO_TEXT_CONTENT}\n"""\n\n'
        f"{ask}"
    )


def memory_section(entries: Sequence[tuple[str, str]]) -> str:
    """Render explicitly saved memories as part of the system instructions."""
    lines = [f"- {key}: {value}" for key, value in entries]
    return (
        "The member asked you to remember the following about them. Use it when it is "
        "relevant and do not contradict it.\n" + "\n".join(lines)
    )
