"""Composite 2D paths: contours (single loops) and multi-loop paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

from antcam_rc2.core.geometry.curves import Curve2, LineSegment
from antcam_rc2.core.geometry.primitives import Box2, Point2
from antcam_rc2.core.units import TOLERANCE_MM

if TYPE_CHECKING:
    from antcam_rc2.core.geometry.transform import Affine2D

__all__ = ["Contour", "Path", "Orientation", "orientation"]


class Orientation(str):
    """Loop orientation for CAM conventions.

    External boundaries are normalised to ``CCW``; holes to ``CW``.
    """

    CCW: Orientation = "ccw"  # ty: ignore[invalid-assignment]
    CW: Orientation = "cw"  # ty: ignore[invalid-assignment]


class Contour:
    """A single connected chain of curves.

    A contour is *closed* when the end point of the last segment coincides with
    the start point of the first segment.  Segments are expected to be
    chained: each segment starts where the previous one ended.
    """

    __slots__ = ("segments",)

    def __init__(self, segments: list[Curve2]) -> None:
        if not segments:
            raise ValueError("Contour requires at least one segment")
        self.segments = segments

    @property
    def start(self) -> Point2:
        """The first point of the contour."""
        return self.segments[0].start

    @property
    def end(self) -> Point2:
        """The last point of the contour."""
        return self.segments[-1].end

    def is_closed(self) -> bool:
        """True when the contour forms a closed loop."""
        return self.end.distance_to(self.start) <= TOLERANCE_MM

    @property
    def closed(self) -> bool:
        return self.is_closed()

    def length(self) -> float:
        """Total arc length of all segments."""
        return sum(seg.length() for seg in self.segments)

    def area(self) -> float:
        """Signed area computed with the shoelace formula over the polyline.

        Positive for CCW loops, negative for CW loops.
        """
        total = 0.0
        for seg in self.segments:
            pts = seg.to_polyline()
            for a, b in zip(pts, pts[1:], strict=False):
                total += a.x * b.y - b.x * a.y
        return total / 2.0

    def orientation(self) -> Orientation:
        """The loop orientation based on the sign of the signed area."""
        return Orientation.CCW if self.area() >= 0.0 else Orientation.CW

    def reverse(self) -> Contour:
        """Return a new contour traversed in the opposite direction."""
        return Contour([seg.reverse() for seg in reversed(self.segments)])

    def transform(self, affine: Affine2D) -> Contour:
        """Return the contour transformed by ``affine``."""
        return Contour([seg.transform(affine) for seg in self.segments])

    def to_polyline(self, tolerance: float = TOLERANCE_MM) -> list[Point2]:
        """Flatten the contour to a single list of points."""
        points: list[Point2] = []
        for seg in self.segments:
            pts = seg.to_polyline(tolerance)
            if points and points[-1] == pts[0]:
                points.extend(pts[1:])
            else:
                points.extend(pts)
        return points

    def bounding_box(self) -> Box2:
        """The axis-aligned bounding box of the contour."""
        xs = [seg.start.x for seg in self.segments] + [self.end.x]
        ys = [seg.start.y for seg in self.segments] + [self.end.y]
        return Box2(min(xs), min(ys), max(xs), max(ys))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Contour):
            return NotImplemented
        return len(self.segments) == len(other.segments) and all(
            a == b for a, b in zip(self.segments, other.segments, strict=True)
        )


class Path:
    """A collection of contours forming a compound 2D shape.

    Multiple contours represent islands or holes (e.g. a pocket with a
    central pillar).  The first contour is conventionally the outer boundary.
    """

    __slots__ = ("contours",)

    def __init__(self, contours: list[Contour]) -> None:
        if not contours:
            raise ValueError("Path requires at least one contour")
        self.contours = contours

    def length(self) -> float:
        """Total length of all contours."""
        return sum(c.length() for c in self.contours)

    def area(self) -> float:
        """Net area: outer contours positive, holes negative by orientation."""
        total = 0.0
        for contour in self.contours:
            a = contour.area()
            # Contours are stored as-is; net area uses orientation sign.
            total += a
        return total

    def bounding_box(self) -> Box2:
        """The axis-aligned bounding box of the whole path."""
        boxes = [c.bounding_box() for c in self.contours]
        result = boxes[0]
        for box in boxes[1:]:
            result = result.union(box)
        return result

    def transform(self, affine: Affine2D) -> Path:
        """Return the path transformed by ``affine``."""
        return Path([c.transform(affine) for c in self.contours])

    def to_polyline(self, tolerance: float = TOLERANCE_MM) -> list[list[Point2]]:
        """Flatten every contour to a list of point lists."""
        return [c.to_polyline(tolerance) for c in self.contours]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Path):
            return NotImplemented
        return self.contours == other.contours


def orientation(contour: Contour) -> Orientation:
    """Convenience wrapper for :meth:`Contour.orientation`."""
    return contour.orientation()


def _points_to_contour(points: list[Point2]) -> Contour:
    """Build a closed contour from a list of points (used by offset results)."""
    if len(points) < 3:
        raise ValueError("A closed contour requires at least 3 points")
    if points[0].distance_to(points[-1]) <= TOLERANCE_MM:
        points = points[:-1]
    segments: list[Curve2] = []
    for a, b in zip(points, points[1:] + [points[0]], strict=True):
        if a.distance_to(b) > TOLERANCE_MM:
            segments.append(LineSegment(a, b))
    if not segments:
        raise ValueError("Contour has no segments after removing degenerate points")
    return Contour(segments)


def _polygon_to_contours(polygon) -> list[Contour]:
    """Convert a shapely polygon into native contours (outer + holes).

    The outer ring is emitted first, then holes; each is converted via
    :func:`_points_to_contour`.
    """
    from shapely.geometry import Polygon

    if isinstance(polygon, Polygon):
        contours = [_points_to_contour([Point2(x, y) for x, y in polygon.exterior.coords])]
        for interior in polygon.interiors:
            contours.append(_points_to_contour([Point2(x, y) for x, y in interior.coords]))
        return contours
    raise TypeError(f"Cannot convert {type(polygon).__name__} to contours")
