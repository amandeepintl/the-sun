"""Typed error taxonomy shared by every layer.

The classification carried here (``retryable``, ``public_message``) is consumed
by the provider and service layers so transient upstream failures can be retried
or failed over while permanent ones surface as safe, actionable text. Strings in
this module describe failures only - they are never presented as AI output.
"""

from __future__ import annotations

__all__ = [
    "CacheUnavailableError",
    "ConfigurationError",
    "InvalidInputError",
    "PermissionDeniedError",
    "PersistenceError",
    "ProviderAuthError",
    "ProviderBadResponseError",
    "ProviderCircuitOpenError",
    "ProviderError",
    "ProviderNotConfiguredError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderUpstreamError",
    "QuotaExceededError",
    "RateLimitedError",
    "TheSunError",
]


class TheSunError(Exception):
    """Base class for every error raised by this project.

    ``retryable`` tells callers whether the operation is worth attempting again
    (or handing to a fallback provider); ``public_message`` is the text that may
    be shown to a Discord user. Both default to the conservative choice.
    """

    retryable: bool = False
    public_message: str = "Something went wrong while handling that request."

    def __init__(self, message: str | None = None, *, public_message: str | None = None):
        super().__init__(message or self.__class__.__name__)
        if public_message is not None:
            self.public_message = public_message


# --------------------------------------------------------------------------- #
# Configuration / input
# --------------------------------------------------------------------------- #
class ConfigurationError(TheSunError):
    """The process was started with missing or unusable configuration."""

    public_message = "The bot is misconfigured; an administrator needs to check its settings."


class InvalidInputError(TheSunError):
    """A caller supplied input that cannot be used as given."""

    public_message = "That input could not be used. Please check it and try again."


# --------------------------------------------------------------------------- #
# Infrastructure
# --------------------------------------------------------------------------- #
class PersistenceError(TheSunError):
    """A database operation failed."""

    retryable = True
    public_message = "I could not reach my database just now. Please try again shortly."


class CacheUnavailableError(TheSunError):
    """A Redis operation failed."""

    retryable = True
    public_message = "I could not reach my cache just now. Please try again shortly."


class RateLimitedError(TheSunError):
    """The caller exceeded a configured rate limit or quota."""

    public_message = "You are sending requests too quickly. Please wait a moment."

    def __init__(self, message: str | None = None, *, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class QuotaExceededError(TheSunError):
    """The guild has used up its configured token allowance for the day."""

    public_message = (
        "This server has reached its daily token allowance for The Sun. "
        "An administrator can raise it."
    )


class PermissionDeniedError(TheSunError):
    """The caller is not allowed to perform the requested operation."""

    public_message = "You do not have permission to do that."


# --------------------------------------------------------------------------- #
# AI providers
# --------------------------------------------------------------------------- #
class ProviderError(TheSunError):
    """Base class for AI provider failures."""

    public_message = "The AI provider could not answer that request."

    def __init__(
        self,
        message: str | None = None,
        *,
        provider: str | None = None,
        status_code: int | None = None,
        public_message: str | None = None,
    ):
        super().__init__(message, public_message=public_message)
        self.provider = provider
        self.status_code = status_code


class ProviderNotConfiguredError(ProviderError):
    """No provider is configured, or the requested one does not exist."""

    public_message = "No AI provider is configured for this request."


class ProviderCircuitOpenError(ProviderError):
    """Every candidate provider is temporarily sidelined after repeated failures."""

    retryable = True
    public_message = (
        "All AI providers are temporarily unavailable after repeated failures. "
        "Please try again in a minute."
    )


class ProviderAuthError(ProviderError):
    """The provider rejected our credentials."""

    public_message = (
        "The AI provider rejected its credentials; an administrator must rotate the key."
    )


class ProviderRateLimitError(ProviderError):
    """The provider asked us to slow down."""

    retryable = True
    public_message = "The AI provider is rate limiting requests. Please try again in a moment."

    #: Seconds the provider asked us to wait, when it told us. ``None`` when unknown.
    retry_after_seconds: float | None = None


class ProviderTimeoutError(ProviderError):
    """The provider did not answer within the configured timeout."""

    retryable = True
    public_message = "The AI provider timed out. Please try again."


class ProviderBadResponseError(ProviderError):
    """The provider answered with something we cannot interpret."""

    public_message = "The AI provider returned an unusable response."


class ProviderUpstreamError(ProviderError):
    """The provider reported a server-side failure."""

    retryable = True
    public_message = "The AI provider is having trouble right now. Please try again shortly."
