"""Instruction templates and transcript rendering.

These are the strings the model receives. The tests check the properties that
matter: real input is carried through unchanged, quoted messages keep their author
and order, empty messages are marked rather than dropped, and nothing is invented.
"""

from __future__ import annotations

from datetime import UTC, datetime

from the_sun.config import DEFAULT_SYSTEM_PROMPT
from the_sun.services.prompts import (
    NO_TEXT_CONTENT,
    TranscriptEntry,
    answer_instruction,
    explain_instruction,
    memory_section,
    message_instruction,
    render_transcript,
    summarize_instruction,
    translate_instruction,
)

STMAP = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _entry(author: str, content: str, **overrides: object) -> TranscriptEntry:
    values: dict[str, object] = {"author": author, "content": content, "timestamp": STMAP}
    values.update(overrides)
    return TranscriptEntry(**values)  # type: ignore[arg-type]


def test_default_system_prompt_is_instructions_not_data() -> None:
    assert "The Sun" in DEFAULT_SYSTEM_PROMPT
    assert "{" not in DEFAULT_SYSTEM_PROMPT, "the default prompt must not be a template"


def test_answer_instruction_passes_the_question_through() -> None:
    assert answer_instruction("  What is PostgreSQL?  ") == "What is PostgreSQL?"


def test_render_transcript_keeps_order_authors_and_every_message() -> None:
    rendered = render_transcript(
        [
            _entry("ada", "first"),
            _entry("grace", "second", is_bot=True),
            _entry("ada", "", has_attachments=True),
        ],
        max_chars_per_message=100,
    )
    lines = rendered.splitlines()
    assert len(lines) == 3, "no message may be dropped"
    assert lines[0].endswith("ada: first")
    assert lines[1].endswith("grace (bot): second")
    assert lines[2].endswith("ada: [attachment]")
    assert lines[2].startswith("[2026-09-20 12:00]")


def test_empty_message_without_attachments_is_marked() -> None:
    rendered = render_transcript([_entry("ada", "   ")], max_chars_per_message=100)
    assert rendered.endswith(f"ada: {NO_TEXT_CONTENT}")


def test_long_messages_are_clipped_and_whitespace_collapsed() -> None:
    rendered = render_transcript(
        [_entry("ada", "line one\n\nline two   and more"), _entry("grace", "y" * 50)],
        max_chars_per_message=30,
    )
    first, second = rendered.splitlines()
    assert first.endswith("ada: line one line two and more"), "runs of whitespace are collapsed"
    body = second.split(": ", 1)[1]
    assert body == "y" * 29 + "\u2026"
    assert len(body) == 30, "clipping never exceeds the configured limit"


def test_explain_instruction_names_the_language_when_given() -> None:
    with_language = explain_instruction("def f(): ...", language="Python")
    assert "Python code or concept" in with_language
    assert with_language.rstrip().endswith("def f(): ...")
    assert "code or concept" in explain_instruction("recursion")


def test_translate_instruction_states_the_direction() -> None:
    assert "into Dutch (detect the source language yourself)" in translate_instruction(
        "hello", target_language="Dutch"
    )
    explicit = translate_instruction("hello", target_language="Dutch", source_language="English")
    assert "from English into Dutch" in explicit
    assert explicit.rstrip().endswith("hello")


def test_summarize_instruction_quotes_the_real_transcript() -> None:
    instruction = summarize_instruction(
        "[2026-09-20 12:00] ada: ship it",
        message_count=1,
        channel_name="general",
        focus="deadlines",
    )
    assert "1 consecutive messages" in instruction
    assert "#general" in instruction
    assert "deadlines" in instruction
    assert "[2026-09-20 12:00] ada: ship it" in instruction
    assert "Do not invent" in instruction


def test_message_instruction_includes_author_and_default_request() -> None:
    instruction = message_instruction("rollback the deploy", author="ada")
    assert "sent by ada" in instruction
    assert "rollback the deploy" in instruction
    asked = message_instruction("rollback the deploy", author="ada", request="Rewrite this nicely")
    assert asked.rstrip().endswith("Rewrite this nicely")


def test_message_instruction_marks_message_without_text() -> None:
    assert NO_TEXT_CONTENT in message_instruction("", author="ada")


def test_memory_section_lists_member_supplied_facts() -> None:
    section = memory_section([("timezone", "UTC+2"), ("units", "metric")])
    assert "- timezone: UTC+2" in section
    assert "- units: metric" in section
    assert "asked you to remember" in section
