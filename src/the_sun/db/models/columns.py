"""Column helpers shared by the models."""

from __future__ import annotations

from enum import Enum as PythonEnum

from sqlalchemy import Enum as SQLEnum

__all__ = ["enum_column"]


def enum_column[EnumT: PythonEnum](enum_class: type[EnumT], *, name: str) -> SQLEnum:
    """Build a check-constrained VARCHAR column that stores enum *values*.

    ``native_enum=False`` keeps migrations simple and ``create_constraint=True``
    keeps the database honest about which values are legal. Persisting ``.value``
    rather than ``.name`` means the stored text matches the lowercase values the
    rest of the codebase uses.
    """
    return SQLEnum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        validate_strings=True,
        values_callable=lambda cls: [member.value for member in cls],
    )
