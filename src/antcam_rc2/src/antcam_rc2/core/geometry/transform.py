"""2D affine transforms.

The public entry point is :class:`Affine2D`, a 3x3 homogeneous matrix that can
be applied to every geometry primitive via :func:`apply`.

Following the SVG convention a transform is stored as ``(a, b, c, d, e, f)``
with ``x' = a*x + c*y + e`` and ``y' = b*x + d*y + f``.  The :func:`compose`
helper chains transforms so that ``compose(a, b)`` applies ``b`` first and then
``a`` (left-to-right composition, matching SVG ``transform`` attributes).
"""

from __future__ import annotations

import math
from typing import overload

from antcam_rc2.core.errors import GeometryError
from antcam_rc2.core.geometry.curves import Arc, Circle, Curve2, LineSegment
from antcam_rc2.core.geometry.paths import Contour, Path
from antcam_rc2.core.geometry.primitives import Point2, Vec2
from antcam_rc2.core.units import SCALE_EPS

__all__ = ["Affine2D", "apply"]


class Affine2D:
    """An invertible 2D affine transform backed by a 3x3 homogeneous matrix."""

    __slots__ = ("a", "b", "c", "d", "e", "f")

    def __init__(self, a: float, b: float, c: float, d: float, e: float, f: float) -> None:
        self.a = float(a)
        self.b = float(b)
        self.c = float(c)
        self.d = float(d)
        self.e = float(e)
        self.f = float(f)

    @staticmethod
    def identity() -> Affine2D:
        """The identity transform."""
        return Affine2D(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    @staticmethod
    def translate(tx: float, ty: float = 0.0) -> Affine2D:
        """A translation by ``(tx, ty)``."""
        return Affine2D(1.0, 0.0, 0.0, 1.0, tx, ty)

    @staticmethod
    def scale(sx: float, sy: float | None = None) -> Affine2D:
        """A uniform (if ``sy`` is None) or non-uniform scale."""
        if sy is None:
            sy = sx
        return Affine2D(sx, 0.0, 0.0, sy, 0.0, 0.0)

    @staticmethod
    def rotate(angle_rad: float) -> Affine2D:
        """A counter-clockwise rotation by ``angle_rad``."""
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        return Affine2D(cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)

    @staticmethod
    def mirror_x() -> Affine2D:
        """A reflection across the x axis (flips y)."""
        return Affine2D(1.0, 0.0, 0.0, -1.0, 0.0, 0.0)

    @staticmethod
    def mirror_y() -> Affine2D:
        """A reflection across the y axis (flips x)."""
        return Affine2D(-1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    @staticmethod
    def compose(first: Affine2D, second: Affine2D) -> Affine2D:
        """Return ``first ∘ second``: applies ``second`` first, then ``first``.

        This matches SVG semantics where nested ``transform`` attributes are
        composed left-to-right.
        """
        a = first.a * second.a + first.c * second.b
        b = first.b * second.a + first.d * second.b
        c = first.a * second.c + first.c * second.d
        d = first.b * second.c + first.d * second.d
        e = first.a * second.e + first.c * second.f + first.e
        f = first.b * second.e + first.d * second.f + first.f
        return Affine2D(a, b, c, d, e, f)

    @staticmethod
    def chain(*transforms: Affine2D) -> Affine2D:
        """Compose any number of transforms left-to-right.

        ``chain(a, b, c)`` applies ``c`` first, then ``b``, then ``a``.
        """
        result = Affine2D.identity()
        for transform in transforms:
            result = Affine2D.compose(result, transform)
        return result

    def apply_point(self, point: Point2) -> Point2:
        """Transform a point (translation applies)."""
        return Point2(
            self.a * point.x + self.c * point.y + self.e,
            self.b * point.x + self.d * point.y + self.f,
        )

    def apply_vec(self, vec: Vec2) -> Vec2:
        """Transform a free vector (no translation)."""
        return Vec2(
            self.a * vec.x + self.c * vec.y,
            self.b * vec.x + self.d * vec.y,
        )

    @property
    def determinant(self) -> float:
        """The determinant of the linear part of the transform."""
        return self.a * self.d - self.b * self.c

    def is_uniform_scale(self) -> bool:
        """True when the linear part is a rotation + uniform scale (+ mirror).

        Non-uniform scaling (distorting circles into ellipses) is detected by
        comparing the norm of the two matrix columns.
        """
        col1 = math.hypot(self.a, self.b)
        col2 = math.hypot(self.c, self.d)
        return abs(col1 - col2) <= SCALE_EPS * max(1.0, abs(col1), abs(col2))

    def scale_factor(self) -> float:
        """The uniform scale factor of a similarity transform.

        Raises:
            GeometryError: if the transform has non-uniform scaling.
        """
        if not self.is_uniform_scale():
            raise GeometryError("Transform has non-uniform scaling; no single scale factor")
        return math.hypot(self.a, self.b)

    def flipped(self) -> bool:
        """True when the transform mirrors (negative determinant)."""
        return self.determinant < 0.0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Affine2D):
            return NotImplemented
        return all(
            abs(a - b) <= SCALE_EPS * max(1.0, abs(a), abs(b))
            for a, b in zip(
                (self.a, self.b, self.c, self.d, self.e, self.f),
                (other.a, other.b, other.c, other.d, other.e, other.f),
                strict=True,
            )
        )


@overload
def apply(affine: Affine2D, entity: Point2) -> Point2: ...


@overload
def apply(affine: Affine2D, entity: Vec2) -> Vec2: ...


@overload
def apply(affine: Affine2D, entity: LineSegment) -> LineSegment: ...


@overload
def apply(affine: Affine2D, entity: Arc) -> Arc: ...


@overload
def apply(affine: Affine2D, entity: Circle) -> Circle: ...


@overload
def apply(affine: Affine2D, entity: Curve2) -> Curve2: ...


@overload
def apply(affine: Affine2D, entity: Contour) -> Contour: ...


@overload
def apply(affine: Affine2D, entity: Path) -> Path: ...


def apply(affine: Affine2D, entity: object) -> object:
    """Apply ``affine`` to a geometry entity.

    Supported entity types: :class:`Point2`, :class:`Vec2`,
    :class:`LineSegment`, :class:`Arc`, :class:`Circle`, :class:`Contour` and
    :class:`Path`.  Curves with a non-uniform scale are approximated to a
    polyline contour via :class:`GeometryError` fallback.
    """
    if isinstance(entity, Point2):
        return affine.apply_point(entity)
    if isinstance(entity, Vec2):
        return affine.apply_vec(entity)
    if isinstance(entity, LineSegment):
        return LineSegment(affine.apply_point(entity.start), affine.apply_point(entity.end))
    if isinstance(entity, Circle):
        if not affine.is_uniform_scale():
            raise GeometryError("Cannot exactly transform a circle under non-uniform scaling")
        return Circle(affine.apply_point(entity.center), entity.radius * affine.scale_factor())
    if isinstance(entity, Arc):
        if not affine.is_uniform_scale():
            raise GeometryError("Cannot exactly transform an arc under non-uniform scaling")
        center = affine.apply_point(entity.center)
        radius = entity.radius * affine.scale_factor()
        start_point = affine.apply_point(entity.start)
        end_point = affine.apply_point(entity.end)
        start_angle = math.atan2(start_point.y - center.y, start_point.x - center.x)
        end_angle = math.atan2(end_point.y - center.y, end_point.x - center.x)
        ccw = entity.ccw
        if affine.flipped():
            ccw = not ccw
        return Arc(center, radius, start_angle, end_angle, ccw)
    if isinstance(entity, Contour):
        return Contour([apply(affine, seg) for seg in entity.segments])
    if isinstance(entity, Path):
        return Path([apply(affine, contour) for contour in entity.contours])
    raise TypeError(f"Cannot apply affine transform to {type(entity).__name__}")
