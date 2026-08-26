"""2D geometric primitives: points, vectors and bounding boxes."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from antcam_rc2.core.units import SCALE_EPS, TOLERANCE_MM

__all__ = ["Point2", "Vec2", "Box2"]


@dataclass(frozen=True, eq=False)
class Point2:
    """A point in 2D space.

    Equality is geometric: two points compare equal when their distance is
    within ``TOLERANCE_MM``.  This mirrors the tolerant nature of CAM data.
    """

    x: float
    y: float

    def distance_to(self, other: Point2) -> float:
        """Euclidean distance to another point."""
        return math.hypot(self.x - other.x, self.y - other.y)

    def __add__(self, other: Point2) -> Point2:
        return Point2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Point2) -> Point2:
        return Point2(self.x - other.x, self.y - other.y)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Point2):
            return NotImplemented
        return self.distance_to(other) <= TOLERANCE_MM

    def __hash__(self) -> int:
        # Round to a grid at TOLERANCE_MM so tolerant equality and hashing
        # agree for points that are within tolerance of each other.
        gx = round(self.x / TOLERANCE_MM)
        gy = round(self.y / TOLERANCE_MM)
        return hash((gx, gy))


@dataclass(frozen=True)
class Vec2:
    """A 2D vector (free vector, not tied to a location)."""

    x: float
    y: float

    @property
    def length(self) -> float:
        """Euclidean length of the vector."""
        return math.hypot(self.x, self.y)

    def normalize(self) -> Vec2:
        """Return the unit vector in the same direction.

        Raises:
            ZeroDivisionError: if the vector has zero length.
        """
        length = self.length
        if length <= SCALE_EPS:
            raise ZeroDivisionError("Cannot normalize a zero-length vector")
        return Vec2(self.x / length, self.y / length)

    def dot(self, other: Vec2) -> float:
        """Dot product with another vector."""
        return self.x * other.x + self.y * other.y

    def cross(self, other: Vec2) -> float:
        """Scalar cross product (z component of the 3D cross product)."""
        return self.x * other.y - self.y * other.x

    def rotate(self, angle_rad: float) -> Vec2:
        """Return this vector rotated by ``angle_rad`` counter-clockwise."""
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        return Vec2(self.x * cos_a - self.y * sin_a, self.x * sin_a + self.y * cos_a)


@dataclass(frozen=True, eq=False)
class Box2:
    """An axis-aligned 2D bounding box.

    Attributes:
        min_x: minimum x coordinate (inclusive).
        min_y: minimum y coordinate (inclusive).
        max_x: maximum x coordinate (inclusive).
        max_y: maximum y coordinate (inclusive).
    """

    min_x: float = field(default=0.0)
    min_y: float = field(default=0.0)
    max_x: float = field(default=0.0)
    max_y: float = field(default=0.0)

    @property
    def width(self) -> float:
        """Width of the box along x."""
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        """Height of the box along y."""
        return self.max_y - self.min_y

    @property
    def center(self) -> Point2:
        """Geometric centre of the box."""
        return Point2((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)

    def union(self, other: Box2) -> Box2:
        """The smallest box containing both this and ``other``."""
        return Box2(
            min_x=min(self.min_x, other.min_x),
            min_y=min(self.min_y, other.min_y),
            max_x=max(self.max_x, other.max_x),
            max_y=max(self.max_y, other.max_y),
        )

    def contains(self, point: Point2) -> bool:
        """True when ``point`` lies inside (or on the edge of) the box."""
        return self.min_x <= point.x <= self.max_x and self.min_y <= point.y <= self.max_y

    def expand(self, margin: float) -> Box2:
        """Return a box grown by ``margin`` on every side."""
        return Box2(
            min_x=self.min_x - margin,
            min_y=self.min_y - margin,
            max_x=self.max_x + margin,
            max_y=self.max_y + margin,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Box2):
            return NotImplemented
        return all(abs(a - b) <= TOLERANCE_MM for a, b in zip(self.to_tuple(), other.to_tuple(), strict=True))

    def __hash__(self) -> int:
        return hash(tuple(round(v / TOLERANCE_MM) for v in self.to_tuple()))

    def to_tuple(self) -> tuple[float, float, float, float]:
        """Return ``(min_x, min_y, max_x, max_y)``."""
        return (self.min_x, self.min_y, self.max_x, self.max_y)
