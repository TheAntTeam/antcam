"""Entry, exit, and link motions for toolpath generation.

All helper functions are pure and return validated :class:`MotionCommand`
lists; strategies append them through :meth:`MotionBuilder.append`.
"""

from __future__ import annotations

import math

from antcam_rc2.core.geometry.primitives import Point2, Vec2
from antcam_rc2.core.toolpath.models import MotionCommand, MotionKind, Position3


def build_entry_sequence(
    start_xy: Point2,
    clearance_z: float,
    depth_z: float,
    plunge_feed: float,
    *,
    use_ramp: bool = False,
    ramp_length_mm: float | None = None,
    ramp_angle_deg: float | None = None,
) -> list[MotionCommand]:
    """Build the standard entry: rapid to clearance, then plunge or ramp.

    With ``use_ramp`` a linear ramp descends from ``clearance_z`` to
    ``depth_z`` over ``ramp_length_mm`` starting ``ramp_length_mm`` before
    ``start_xy`` (i.e. the ramp direction is reversed from the first cut).
    Without a ramp the tool plunges vertically at ``start_xy``.

    Args:
        start_xy: the first cut point.
        clearance_z: absolute rapid Z above the stock top.
        depth_z: absolute cutting Z for this pass.
        plunge_feed: feed used for plunging/ramping.
        use_ramp: enable the linear ramp entry.
        ramp_length_mm: horizontal ramp length (defaults to 1.0 mm).
        ramp_angle_deg: kept for interface compatibility; the ramp is linear
            and its slope is derived from ``ramp_length_mm`` and the Z drop.
    """
    motions: list[MotionCommand] = [
        MotionCommand(
            kind=MotionKind.RAPID,
            endpoint=Position3(x_mm=start_xy.x, y_mm=start_xy.y, z_mm=clearance_z),
        )
    ]

    if use_ramp:
        length = max(ramp_length_mm if ramp_length_mm and ramp_length_mm > 0 else 1.0, 1e-6)
        ramp_start = Point2(start_xy.x - length, start_xy.y)
        motions.append(
            MotionCommand(
                kind=MotionKind.RAPID,
                endpoint=Position3(x_mm=ramp_start.x, y_mm=ramp_start.y, z_mm=clearance_z),
            )
        )
        motions.append(
            MotionCommand(
                kind=MotionKind.CUT_LINEAR,
                endpoint=Position3(x_mm=start_xy.x, y_mm=start_xy.y, z_mm=depth_z),
                feed_mm_min=plunge_feed,
            )
        )
    else:
        motions.append(
            MotionCommand(
                kind=MotionKind.CUT_LINEAR,
                endpoint=Position3(x_mm=start_xy.x, y_mm=start_xy.y, z_mm=depth_z),
                feed_mm_min=plunge_feed,
            )
        )

    return motions


def build_exit_sequence(
    end_xy: Point2,
    clearance_z: float,
    depth_z: float,
) -> list[MotionCommand]:
    """Build the standard exit: rapid retract from the cut depth to clearance.

    Tangent lead-outs are computed with :func:`build_leadout` by strategies
    that have access to the previous point and the cut feed.
    """
    return [
        MotionCommand(
            kind=MotionKind.RAPID,
            endpoint=Position3(x_mm=end_xy.x, y_mm=end_xy.y, z_mm=clearance_z),
        )
    ]


def build_leadout(
    end_xy: Point2,
    prev_xy: Point2,
    depth_z: float,
    cut_feed: float,
    leadout_length_mm: float,
) -> MotionCommand:
    """Return a tangent lead-out cut extending past ``end_xy``.

    The lead-out continues the direction from ``prev_xy`` to ``end_xy`` for
    ``leadout_length_mm`` at ``depth_z`` — the standard climb-cut lead-out.
    """
    dx = end_xy.x - prev_xy.x
    dy = end_xy.y - prev_xy.y
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        direction = Vec2(1.0, 0.0)
    else:
        direction = Vec2(dx / length, dy / length)
    target = Point2(end_xy.x + direction.x * leadout_length_mm, end_xy.y + direction.y * leadout_length_mm)
    return MotionCommand(
        kind=MotionKind.CUT_LINEAR,
        endpoint=Position3(x_mm=target.x, y_mm=target.y, z_mm=depth_z),
        feed_mm_min=cut_feed,
    )


def build_link_motion(
    from_xy: Point2,
    to_xy: Point2,
    clearance_z: float,
) -> list[MotionCommand]:
    """Build the rapid link between two contours at clearance height."""
    return [
        MotionCommand(
            kind=MotionKind.RAPID,
            endpoint=Position3(x_mm=from_xy.x, y_mm=from_xy.y, z_mm=clearance_z),
        ),
        MotionCommand(
            kind=MotionKind.RAPID,
            endpoint=Position3(x_mm=to_xy.x, y_mm=to_xy.y, z_mm=clearance_z),
        ),
    ]


def can_ramp(
    start_xy: Point2,
    end_xy: Point2,
    depth_z: float,
    clearance_z: float,
    tool_diameter_mm: float,
    min_ramp_length_mm: float = 1.0,
) -> tuple[bool, float | None]:
    """Check whether a -X ramp entry fits before ``start_xy``.

    The default ramp approaches along -X (see :func:`build_entry_sequence`);
    it is feasible only when there is at least ``min_ramp_length_mm`` of room
    before the start point without crossing the previous point ``end_xy``.
    Returns ``(feasible, ramp_length)``.
    """
    available = start_xy.x - end_xy.x
    if available >= min_ramp_length_mm:
        return True, min_ramp_length_mm
    return False, None


def can_lead_out(
    end_xy: Point2,
    prev_xy: Point2,
    tool_diameter_mm: float,
    min_leadout_mm: float = 1.0,
) -> tuple[bool, Point2 | None]:
    """Check whether a lead-out fits and return its target point.

    Returns ``(feasible, leadout_point)``; the lead-out is tangent to the
    final segment direction.
    """
    dx = end_xy.x - prev_xy.x
    dy = end_xy.y - prev_xy.y
    length = math.hypot(dx, dy)
    if length < min_leadout_mm:
        return False, None
    scale = min_leadout_mm / length
    return True, Point2(x=end_xy.x + dx * scale, y=end_xy.y + dy * scale)
