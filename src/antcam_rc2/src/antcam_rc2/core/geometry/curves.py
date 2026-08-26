"""2D curve primitives: lines, arcs and circles.

All curves share the :class:`Curve2` interface so downstream code can treat
them uniformly.  Curves are immutable and unit-agnostic (coordinates are
expected to be in millimetres once a scene is normalised).
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.units import TOLERANCE_MM

if TYPE_CHECKING:
    from antcam_rc2.core.geometry.transform import Affine2D

__all__ = ["Curve2", "LineSegment", "Arc", "Circle"]


class Curve2(ABC):
    """Abstract base for 2D curves with a common evaluation interface."""

    @property
    @abstractmethod
    def start(self) -> Point2:
        """The point at parameter ``t = 0``."""

    @property
    @abstractmethod
    def end(self) -> Point2:
        """The point at parameter ``t = 1``."""

    @abstractmethod
    def length(self) -> float:
        """Total arc length of the curve."""

    @abstractmethod
    def point_at(self, t: float) -> Point2:
        """Evaluate the curve at parameter ``t`` in ``[0, 1]``."""

    @abstractmethod
    def reverse(self) -> Curve2:
        """Return a new curve with swapped direction."""

    @abstractmethod
    def transform(self, affine: Affine2D) -> Curve2:
        """Return the curve transformed by ``affine``."""

    @abstractmethod
    def to_polyline(self, tolerance: float = TOLERANCE_MM) -> list[Point2]:
        """Approximate the curve as a list of points (including both ends)."""


class LineSegment(Curve2):
    """A straight segment between two points."""

    __slots__ = ("_start", "_end")

    def __init__(self, start: Point2, end: Point2) -> None:
        self._start = start
        self._end = end

    @property
    def start(self) -> Point2:
        return self._start

    @property
    def end(self) -> Point2:
        return self._end

    def length(self) -> float:
        return self._start.distance_to(self._end)

    def point_at(self, t: float) -> Point2:
        return Point2(
            self._start.x + (self._end.x - self._start.x) * t,
            self._start.y + (self._end.y - self._start.y) * t,
        )

    def reverse(self) -> LineSegment:
        return LineSegment(self._end, self._start)

    def transform(self, affine: Affine2D) -> LineSegment:
        return LineSegment(affine.apply_point(self._start), affine.apply_point(self._end))

    def to_polyline(self, tolerance: float = TOLERANCE_MM) -> list[Point2]:
        return [self._start, self._end]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LineSegment):
            return NotImplemented
        return self._start == other._start and self._end == other._end

    def __hash__(self) -> int:
        return hash((self._start, self._end))

    def __repr__(self) -> str:
        return f"LineSegment({self._start!r}, {self._end!r})"


class Circle(Curve2):
    """A full circle defined by centre and radius."""

    __slots__ = ("_center", "_radius")

    def __init__(self, center: Point2, radius: float) -> None:
        if radius < 0.0:
            raise ValueError(f"Circle radius must be non-negative, got {radius}")
        self._center = center
        self._radius = radius

    @property
    def center(self) -> Point2:
        return self._center

    @property
    def radius(self) -> float:
        return self._radius

    @property
    def start(self) -> Point2:
        return Point2(self._center.x + self._radius, self._center.y)

    @property
    def end(self) -> Point2:
        return self.start

    def length(self) -> float:
        return 2.0 * math.pi * self._radius

    def point_at(self, t: float) -> Point2:
        angle = 2.0 * math.pi * t
        return Point2(
            self._center.x + self._radius * math.cos(angle),
            self._center.y + self._radius * math.sin(angle),
        )

    def reverse(self) -> Circle:
        return Circle(self._center, self._radius)

    def transform(self, affine: Affine2D) -> Circle:
        from antcam_rc2.core.errors import GeometryError

        if not affine.is_uniform_scale():
            raise GeometryError("Circle transform requires uniform scaling")
        return Circle(affine.apply_point(self._center), self._radius * affine.scale_factor())

    def to_polyline(self, tolerance: float = TOLERANCE_MM) -> list[Point2]:
        if self._radius <= tolerance:
            return [self.start]
        segments = _arc_segment_count(self._radius, self._radius, tolerance)
        step = 2.0 * math.pi / segments
        return [
            Point2(
                self._center.x + self._radius * math.cos(angle),
                self._center.y + self._radius * math.sin(angle),
            )
            for i in range(segments + 1)
            for angle in [i * step]
        ]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Circle):
            return NotImplemented
        return self._center == other._center and self._radius == other._radius

    def __hash__(self) -> int:
        return hash((self._center, round(self._radius / TOLERANCE_MM)))

    def __repr__(self) -> str:
        return f"Circle({self._center!r}, {self._radius!r})"


class Arc(Curve2):
    """A circular arc.

    The arc runs from ``start_angle`` to ``end_angle`` (radians) around
    ``center`` in the direction given by ``ccw``.
    """

    __slots__ = ("_center", "_radius", "_start_angle", "_end_angle", "_ccw")

    def __init__(
        self,
        center: Point2,
        radius: float,
        start_angle: float,
        end_angle: float,
        ccw: bool,
    ) -> None:
        if radius < 0.0:
            raise ValueError(f"Arc radius must be non-negative, got {radius}")
        self._center = center
        self._radius = radius
        self._start_angle = start_angle
        self._end_angle = end_angle
        self._ccw = ccw

    @property
    def center(self) -> Point2:
        return self._center

    @property
    def radius(self) -> float:
        return self._radius

    @property
    def start_angle(self) -> float:
        return self._start_angle

    @property
    def end_angle(self) -> float:
        return self._end_angle

    @property
    def ccw(self) -> bool:
        return self._ccw

    @property
    def start(self) -> Point2:
        return self.point_at(0.0)

    @property
    def end(self) -> Point2:
        return self.point_at(1.0)

    def sweep_angle(self) -> float:
        """The total sweep in radians (always in ``[0, 2*pi)``)."""
        if self._ccw:
            sweep = (self._end_angle - self._start_angle) % (2.0 * math.pi)
        else:
            sweep = (self._start_angle - self._end_angle) % (2.0 * math.pi)
        if sweep <= TOLERANCE_MM:
            return 0.0
        return sweep

    def length(self) -> float:
        return self._radius * self.sweep_angle()

    def point_at(self, t: float) -> Point2:
        sweep = self.sweep_angle()
        if self._ccw:
            angle = self._start_angle + sweep * t
        else:
            angle = self._start_angle - sweep * t
        return Point2(
            self._center.x + self._radius * math.cos(angle),
            self._center.y + self._radius * math.sin(angle),
        )

    def reverse(self) -> Arc:
        return Arc(self._center, self._radius, self._end_angle, self._start_angle, not self._ccw)

    def transform(self, affine: Affine2D) -> Arc:
        from antcam_rc2.core.errors import GeometryError

        if not affine.is_uniform_scale():
            raise GeometryError("Arc transform requires uniform scaling")
        center = affine.apply_point(self._center)
        radius = self._radius * affine.scale_factor()
        start_point = affine.apply_point(self.start)
        end_point = affine.apply_point(self.end)
        start_angle = math.atan2(start_point.y - center.y, start_point.x - center.x)
        end_angle = math.atan2(end_point.y - center.y, end_point.x - center.x)
        ccw = self._ccw
        if affine.flipped():
            ccw = not ccw
        return Arc(center, radius, start_angle, end_angle, ccw)

    def to_polyline(self, tolerance: float = TOLERANCE_MM) -> list[Point2]:
        sweep = self.sweep_angle()
        if sweep <= tolerance / max(self._radius, TOLERANCE_MM):
            return [self.start, self.end]
        segments = _arc_segment_count(self._radius, sweep, tolerance)
        return [self.point_at(i / segments) for i in range(segments + 1)]

    def __repr__(self) -> str:
        return f"Arc({self._center!r}, {self._radius!r}, {self._start_angle!r}, {self._end_angle!r}, ccw={self._ccw!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Arc):
            return NotImplemented
        return (
            self._center == other._center
            and self._radius == other._radius
            and abs(self._start_angle - other._start_angle) <= TOLERANCE_MM
            and abs(self._end_angle - other._end_angle) <= TOLERANCE_MM
            and self._ccw == other._ccw
        )

    def __hash__(self) -> int:
        return hash(
            (
                self._center,
                round(self._radius / TOLERANCE_MM),
                round(self._start_angle / TOLERANCE_MM),
                round(self._end_angle / TOLERANCE_MM),
                self._ccw,
            )
        )


def _arc_segment_count(radius: float, sweep: float, tolerance: float) -> int:
    """Estimate the number of polyline segments for a circular arc.

    The chord error of a single segment of angle ``delta`` is
    ``radius * (1 - cos(delta/2))``; we pick a segment count that keeps this
    error below ``tolerance``.
    """
    if radius <= TOLERANCE_MM:
        return 1
    per_segment = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - tolerance / radius)))
    if per_segment <= 0.0:
        return 1
    return max(1, math.ceil(sweep / per_segment))
