"""Deterministic identifier generation for project entities."""

from __future__ import annotations

import re
import uuid

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*_[0-9a-f]{8}$")
_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def new_id(prefix: str) -> str:
    """Generate a short, unique entity id with the given ``prefix``.

    The prefix must be snake_case (lowercase segments separated by single
    underscores, no leading/trailing underscore). Example: ``op_a1b2c3d4``.
    """
    if _PREFIX_PATTERN.fullmatch(prefix) is None:
        raise ValueError(f"Invalid id prefix: {prefix!r}")
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def is_valid_id(value: str, prefix: str) -> bool:
    """Return whether ``value`` is a valid id generated for ``prefix``."""
    expected_prefix = f"{prefix}_"
    if not value.startswith(expected_prefix):
        return False
    return _ID_PATTERN.fullmatch(value) is not None
