"""Adaptive sampling of parametric curves into polylines.

Used to flatten bezier curves, ellipses and splines into point lists at import
time.  The sampling is *tolerance driven*: the step count is chosen so the
deviation of the polyline from the true curve stays below a requested
tolerance, keeping the output deterministic.
"""

from __future__ import annotations

from collections.abc import Callable

from antcam_rc2.core.geometry.curves import Curve2
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.units import TOLERANCE_MM

__all__ = ["sample_curve", "approximate_to_polyline", "adaptive_sample"]


def sample_curve(curve: Curve2, tolerance: float = TOLERANCE_MM) -> list[Point2]:
    """Flatten any :class:`Curve2` to a polyline.

    Native curves (lines, arcs, circles) use their exact flattened form;
    anything else is approximated adaptively.
    """
    return curve.to_polyline(tolerance)


def approximate_to_polyline(
    point_at: Callable[[float], Point2],
    *,
    start: float = 0.0,
    end: float = 1.0,
    tolerance: float = TOLERANCE_MM,
    max_segments: int = 1024,
) -> list[Point2]:
    """Approximate a parametric curve to a polyline within ``tolerance``.

    The curve is evaluated through ``point_at(t)`` for ``t`` in
    ``[start, end]``.  A uniform subdivision is refined until the midpoint
    deviation (distance of the true midpoint from its chord) stays below
    ``tolerance``.

    Args:
        point_at: evaluator mapping parameter ``t`` to a point.
        start: parameter of the first sample.
        end: parameter of the last sample.
        tolerance: maximum allowed deviation (in mm).
        max_segments: hard cap on the number of segments.

    Returns:
        The list of sampled points (inclusive of both ends).
    """
    segments = min(_initial_segments(tolerance), max_segments)
    while True:
        points = _uniform_sample(point_at, start, end, segments)
        if segments >= max_segments:
            return points
        if _midpoint_error_ok(point_at, points, start, end, segments, tolerance):
            return points
        segments = min(segments * 2, max_segments)


def adaptive_sample(
    point_at: Callable[[float], Point2],
    *,
    start: float = 0.0,
    end: float = 1.0,
    tolerance: float = TOLERANCE_MM,
    max_segments: int = 1024,
) -> list[Point2]:
    """Alias for :func:`approximate_to_polyline` (kept for clarity)."""
    return approximate_to_polyline(point_at, start=start, end=end, tolerance=tolerance, max_segments=max_segments)


def _initial_segments(tolerance: float) -> int:
    if tolerance <= TOLERANCE_MM:
        return 32
    return max(4, min(64, int(1.0 / max(tolerance, TOLERANCE_MM))))


def _uniform_sample(
    point_at: Callable[[float], Point2],
    start: float,
    end: float,
    segments: int,
) -> list[Point2]:
    span = end - start
    return [point_at(start + span * i / segments) for i in range(segments + 1)]


def _midpoint_error_ok(
    point_at: Callable[[float], Point2],
    points: list[Point2],
    start: float,
    end: float,
    segments: int,
    tolerance: float,
) -> bool:
    """True when every chord's true midpoint is within ``tolerance`` of the chord."""
    if tolerance <= TOLERANCE_MM:
        return True
    span = end - start
    for i in range(segments):
        a = points[i]
        b = points[i + 1]
        t_mid = start + span * (i + 0.5) / segments
        true_mid = point_at(t_mid)
        chord_mid = Point2((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
        if true_mid.distance_to(chord_mid) > tolerance:
            return False
    return True
