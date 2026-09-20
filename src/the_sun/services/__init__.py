"""Service layer: orchestration on top of repositories, cache and providers.

Stage A ships the three services whose behaviour can be verified end to end
today: guild settings (cache-aside), usage (recording and statistics) and health
(real dependency checks). Feature services arrive together with the commands that
need them.
"""

from __future__ import annotations

from the_sun.services.ai_gateway import AIGateway, ChatOutcome
from the_sun.services.ai_service import AIAnswer, AIRequest, AIService
from the_sun.services.base import ServiceContext, build_service_context
from the_sun.services.conversation_service import ConversationService, ConversationTurn
from the_sun.services.health_service import CheckResult, HealthReport, HealthService
from the_sun.services.memory_service import MemoryService, normalise_key
from the_sun.services.permissions import CallerContext, PermissionService, is_guild_admin
from the_sun.services.prompt_builder import PromptBuilder
from the_sun.services.prompts import (
    TranscriptEntry,
    explain_instruction,
    message_instruction,
    render_transcript,
    summarize_instruction,
    translate_instruction,
)
from the_sun.services.rate_limit_service import RateLimitDecision, RateLimitService
from the_sun.services.retention_service import RetentionReport, RetentionService
from the_sun.services.settings_service import GuildSettingsView, SettingsService
from the_sun.services.usage_service import UsageRecord, UsageService

__all__ = [
    "AIAnswer",
    "AIGateway",
    "AIRequest",
    "AIService",
    "CallerContext",
    "ChatOutcome",
    "CheckResult",
    "ConversationService",
    "ConversationTurn",
    "GuildSettingsView",
    "HealthReport",
    "HealthService",
    "MemoryService",
    "PermissionService",
    "PromptBuilder",
    "RateLimitDecision",
    "RateLimitService",
    "RetentionReport",
    "RetentionService",
    "ServiceContext",
    "SettingsService",
    "TranscriptEntry",
    "UsageRecord",
    "UsageService",
    "build_service_context",
    "explain_instruction",
    "is_guild_admin",
    "message_instruction",
    "normalise_key",
    "render_transcript",
    "summarize_instruction",
    "translate_instruction",
]
