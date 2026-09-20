"""Cog package.

Cogs are discovered dynamically: any module in this package exposing a ``setup``
function is loaded by the bot, so adding a command group means adding a real
module rather than editing a registry list.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

__all__ = ["discover_cog_modules", "import_cog_module"]

_PACKAGE_NAME = __name__


def import_cog_module(module_name: str) -> ModuleType:
    """Import one cog module by its fully qualified name."""
    return importlib.import_module(module_name)


def discover_cog_modules() -> list[str]:
    """Fully qualified names of every cog module in this package."""
    discovered: list[str] = []
    for module_info in pkgutil.iter_modules(__path__):
        if module_info.ispkg or module_info.name.startswith("_"):
            continue
        if module_info.name == "base":
            continue  # shared helpers, not a command module
        discovered.append(f"{_PACKAGE_NAME}.{module_info.name}")
    return sorted(discovered)
