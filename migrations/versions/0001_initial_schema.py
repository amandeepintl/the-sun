"""Initial schema: guild settings, users, conversations, messages, memories, usage

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-19

Every table here stores information produced by a real Discord interaction. No
row, identifier or value is seeded: guild configuration and user rows are
provisioned on first use.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONVERSATION_SCOPE_VALUES = "'user_channel', 'channel', 'user', 'guild'"
MESSAGE_ROLE_VALUES = "'system', 'user', 'assistant'"
MESSAGE_KIND_VALUES = "'chat', 'summary', 'explain', 'translate', 'context_action'"
MEMORY_SOURCE_VALUES = "'explicit', 'auto'"
USAGE_STATUS_VALUES = "'ok', 'error', 'rate_limited', 'timeout', 'rejected'"
USAGE_SURFACE_VALUES = "'slash_command', 'context_menu', 'system'"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users")),
    )

    op.create_table(
        "guild_settings",
        sa.Column("guild_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("ai_provider", sa.String(length=64), nullable=True),
        sa.Column("ai_model", sa.String(length=128), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("history_length", sa.Integer(), server_default=sa.text("20"), nullable=False),
        sa.Column("memory_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "ephemeral_responses", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("rate_limit_per_user_per_minute", sa.Integer(), nullable=True),
        sa.Column("rate_limit_per_guild_per_minute", sa.Integer(), nullable=True),
        sa.Column("daily_token_quota", sa.Integer(), nullable=True),
        sa.Column(
            "admin_role_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "allowed_channel_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("history_retention_days", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("guild_id", name=op.f("pk_guild_settings")),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"scope IN ({CONVERSATION_SCOPE_VALUES})",
            name=op.f("ck_conversations_conversation_scope"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(
        "ix_conversations_guild_channel_user",
        "conversations",
        ["guild_id", "channel_id", "user_id"],
    )
    op.create_index("ix_conversations_last_activity_at", "conversations", ["last_activity_at"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("discord_guild_id", sa.BigInteger(), nullable=True),
        sa.Column("discord_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("discord_message_id", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("correlation_id", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"role IN ({MESSAGE_ROLE_VALUES})", name=op.f("ck_messages_message_role")
        ),
        sa.CheckConstraint(
            f"kind IN ({MESSAGE_KIND_VALUES})", name=op.f("ck_messages_message_kind")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(
        "ix_messages_conversation_created", "messages", ["conversation_id", "created_at"]
    )
    op.create_index(
        "ix_messages_guild_channel", "messages", ["discord_guild_id", "discord_channel_id"]
    )
    # The retention purge deletes by creation time.
    op.create_index("ix_messages_created_at", "messages", ["created_at"])

    op.create_table(
        "memories",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"source IN ({MEMORY_SOURCE_VALUES})", name=op.f("ck_memories_memory_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
    )
    op.create_index("ix_memories_user_id", "memories", ["user_id"])
    op.create_index(
        "uq_memories_user_key_global",
        "memories",
        ["user_id", "key"],
        unique=True,
        postgresql_where=sa.text("guild_id IS NULL"),
    )
    op.create_index(
        "uq_memories_user_key_scoped",
        "memories",
        ["user_id", "guild_id", "key"],
        unique=True,
        postgresql_where=sa.text("guild_id IS NOT NULL"),
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("command", sa.String(length=64), nullable=False),
        sa.Column("surface", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=32), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"surface IN ({USAGE_SURFACE_VALUES})", name=op.f("ck_usage_events_usage_surface")
        ),
        sa.CheckConstraint(
            f"status IN ({USAGE_STATUS_VALUES})", name=op.f("ck_usage_events_usage_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
    )
    op.create_index("ix_usage_events_guild_occurred", "usage_events", ["guild_id", "occurred_at"])
    op.create_index("ix_usage_events_user_occurred", "usage_events", ["user_id", "occurred_at"])
    op.create_index("ix_usage_events_command_occurred", "usage_events", ["command", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_events_command_occurred", table_name="usage_events")
    op.drop_index("ix_usage_events_user_occurred", table_name="usage_events")
    op.drop_index("ix_usage_events_guild_occurred", table_name="usage_events")
    op.drop_table("usage_events")

    op.drop_index("uq_memories_user_key_scoped", table_name="memories")
    op.drop_index("uq_memories_user_key_global", table_name="memories")
    op.drop_index("ix_memories_user_id", table_name="memories")
    op.drop_table("memories")

    op.drop_index("ix_messages_guild_channel", table_name="messages")
    op.drop_index("ix_messages_created_at", table_name="messages")
    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_conversations_last_activity_at", table_name="conversations")
    op.drop_index("ix_conversations_guild_channel_user", table_name="conversations")
    op.drop_table("conversations")

    op.drop_table("guild_settings")
    op.drop_table("users")
