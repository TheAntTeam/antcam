"""Deterministic safety checks and decomposition of negative-Z depth passes."""

from __future__ import annotations

import math

from antcam_rc2.core.errors import ToolpathError


def plan_depth_passes(
    *,
    top_z_mm: float,
    target_z_mm: float,
    max_stepdown_mm: float,
    stock_bottom_z_mm: float,
) -> tuple[float, ...]:
    """Return monotonic descending Z passes ending exactly at ``target_z_mm``."""
    if not max_stepdown_mm > 0:
        raise ToolpathError("max_stepdown_mm must be positive")
    if target_z_mm >= top_z_mm:
        raise ToolpathError("target_z_mm must be below top_z_mm")
    if target_z_mm < stock_bottom_z_mm:
        raise ToolpathError("target_z_mm must not pass below stock_bottom_z_mm")
    depth = top_z_mm - target_z_mm
    pass_count = math.ceil(depth / max_stepdown_mm)
    return tuple(max(target_z_mm, top_z_mm - max_stepdown_mm * index) for index in range(1, pass_count + 1))
