"""Lua scripts executed atomically inside Redis.

Redis runs a script as one unit, which is what makes the sliding-window counter
correct under concurrent command invocations across shards.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SCRIPTS", "SCRIPT_NAMES", "SLIDING_WINDOW_LIMITER", "Script"]


@dataclass(frozen=True, slots=True)
class Script:
    """A named Lua script and the arguments it expects."""

    name: str
    source: str
    key_arguments: tuple[str, ...]
    value_arguments: tuple[str, ...]


SLIDING_WINDOW_LIMITER = """
-- Atomic sliding-window rate limiter.
-- KEYS[1] = window key
-- ARGV[1] = current time in milliseconds
-- ARGV[2] = window length in milliseconds
-- ARGV[3] = maximum number of events allowed in the window
-- ARGV[4] = unique member for this event
-- Returns {allowed (0|1), count_in_window, retry_after_seconds}
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_after = 0
  if oldest[2] ~= nil then
    retry_after = math.ceil((tonumber(oldest[2]) + window_ms - now_ms) / 1000)
  end
  return {0, count, retry_after}
end

redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)
return {1, count + 1, 0}
"""

SCRIPTS: dict[str, Script] = {
    "sliding_window_limiter": Script(
        name="sliding_window_limiter",
        source=SLIDING_WINDOW_LIMITER,
        key_arguments=("window",),
        value_arguments=("now_ms", "window_ms", "limit", "member"),
    ),
}

SCRIPT_NAMES: frozenset[str] = frozenset(SCRIPTS)
