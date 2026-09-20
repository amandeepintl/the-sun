"""Cache layer: Redis-backed namespaced storage.

``redis`` is imported only inside this package, and every other layer talks to
the :class:`~the_sun.cache.backend.CacheBackend` protocol.
"""

from __future__ import annotations

from the_sun.cache.backend import CacheBackend, CacheHealth
from the_sun.cache.cache_aside import get_or_load, invalidate, read_json, write_json
from the_sun.cache.keys import DEFAULT_TTLS, CacheKeys, CacheNamespace
from the_sun.cache.redis_backend import RedisCacheBackend, create_cache_backend
from the_sun.cache.scripts import SCRIPT_NAMES, SCRIPTS, Script

__all__ = [
    "DEFAULT_TTLS",
    "SCRIPTS",
    "SCRIPT_NAMES",
    "CacheBackend",
    "CacheHealth",
    "CacheKeys",
    "CacheNamespace",
    "RedisCacheBackend",
    "Script",
    "create_cache_backend",
    "get_or_load",
    "invalidate",
    "read_json",
    "write_json",
]
