"""Unit handling and shared numeric constants."""

from __future__ import annotations

from enum import StrEnum

TOLERANCE_MM = 1e-6
SCALE_EPS = 1e-9

MM_PER_INCH = 25.4

Number = int | float


class UnitSystem(StrEnum):
    """Supported unit systems for geometric data."""

    METRIC = "metric"
    IMPERIAL = "imperial"


def mm_to_in(value: Number) -> float:
    """Convert a value from millimetres to inches."""
    return float(value) / MM_PER_INCH


def in_to_mm(value: Number) -> float:
    """Convert a value from inches to millimetres."""
    return float(value) * MM_PER_INCH


def _coerce_system(value: UnitSystem | str) -> UnitSystem:
    if isinstance(value, UnitSystem):
        return value
    try:
        return UnitSystem(value)
    except ValueError:
        raise ValueError(f"Unknown unit system: {value!r}") from None


def convert(value: Number, from_: UnitSystem | str, to: UnitSystem | str) -> float:
    """Convert ``value`` between unit systems.

    Systems may be given as :class:`UnitSystem` members or their string
    values (``"metric"`` / ``"imperial"``).

    Raises:
        ValueError: if either system is unknown.
    """
    source = _coerce_system(from_)
    target = _coerce_system(to)
    if source is target:
        return float(value)
    if source is UnitSystem.METRIC:
        return mm_to_in(value)
    return in_to_mm(value)
