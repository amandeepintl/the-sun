"""The Sun - a production Discord AI assistant.

Stage A implements the backend architecture only: configuration, logging, the
database and repository layers, the Redis cache layer, the AI provider
abstraction and the shared services. The Discord presentation layer (``bot``
and ``cogs``) is built in Stage C on top of these same layers.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:  # pragma: no cover - packaging metadata is present in editable installs
    __version__ = version("the-sun")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["__version__"]
