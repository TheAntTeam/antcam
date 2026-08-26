"""2D geometry primitives, curves, paths, transforms and operations.

The public API of this package exposes only native types.  Shapely and
pyclipper are confined to :mod:`antcam_rc2.core.geometry.ops` and never leak
into the rest of the codebase.
"""

from __future__ import annotations

from antcam_rc2.core.geometry.curves import Arc, Circle, Curve2, LineSegment
from antcam_rc2.core.geometry.paths import Contour, Orientation, Path
from antcam_rc2.core.geometry.primitives import Box2, Point2, Vec2
from antcam_rc2.core.geometry.transform import Affine2D, apply

__all__ = [
    "Affine2D",
    "Arc",
    "Box2",
    "Circle",
    "Contour",
    "Curve2",
    "LineSegment",
    "Orientation",
    "Path",
    "Point2",
    "Vec2",
    "apply",
]
