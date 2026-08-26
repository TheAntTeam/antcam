"""Pure picking and viewport math shared by frontends.

The GPU picking pass (frontend) resolves a pixel to a picking id; this module
turns screen coordinates into 3D rays and intersects them with the work plane,
so a click can be mapped to a world point and, combined with the graph
proximity, to the nearest entity.  All functions are numpy-based, deterministic
and unit-testable without a GL context.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from antcam_rc2.core.geometry.primitives import Point2


@dataclass(frozen=True, slots=True)
class Ray3:
    """A ray in world space: ``origin + t * direction`` for ``t >= 0``."""

    origin: np.ndarray
    direction: np.ndarray

    def __post_init__(self) -> None:
        length = float(np.linalg.norm(self.direction))
        if length <= 1e-12:
            raise ValueError("ray direction must be non-zero")
        object.__setattr__(self, "direction", self.direction / length)


def view_projection_matrix(view: np.ndarray, projection: np.ndarray) -> np.ndarray:
    """Return ``projection @ view`` (4x4, row-major convention used by GL)."""
    return projection @ view


def unproject(
    screen_xy: tuple[float, float],
    viewport_size: tuple[int, int],
    view: np.ndarray,
    projection: np.ndarray,
) -> Ray3:
    """Cast a ray through ``screen_xy`` (pixels, origin top-left).

    The near/far clip points are unprojected through the inverse
    ``projection @ view`` matrix.
    """
    width, height = viewport_size
    if width <= 0 or height <= 0:
        raise ValueError("viewport must be non-empty")
    x_ndc = 2.0 * screen_xy[0] / width - 1.0
    y_ndc = 1.0 - 2.0 * screen_xy[1] / height
    inverse = np.linalg.inv(view_projection_matrix(view, projection))
    near = inverse @ np.array([x_ndc, y_ndc, -1.0, 1.0], dtype=np.float64)
    far = inverse @ np.array([x_ndc, y_ndc, 1.0, 1.0], dtype=np.float64)
    near = near[:3] / near[3]
    far = far[:3] / far[3]
    return Ray3(origin=near, direction=far - near)


def project_point(
    point: tuple[float, float, float],
    view: np.ndarray,
    projection: np.ndarray,
    viewport_size: tuple[int, int],
) -> tuple[float, float]:
    """Project a world point to screen pixels (origin top-left)."""
    width, height = viewport_size
    clip = view_projection_matrix(view, projection) @ np.array([*point, 1.0], dtype=np.float64)
    if abs(clip[3]) <= 1e-12:
        raise ValueError("point lies on the camera plane")
    ndc = clip[:3] / clip[3]
    return ((ndc[0] + 1.0) * 0.5 * width, (1.0 - ndc[1]) * 0.5 * height)


def ray_plane_z(ray: Ray3, z: float) -> Point2 | None:
    """Intersect ``ray`` with the horizontal plane at height ``z``.

    Returns ``None`` when the ray is (near-)parallel to the plane or the
    intersection lies behind the ray origin.
    """
    if abs(ray.direction[2]) <= 1e-9:
        return None
    t = (z - ray.origin[2]) / ray.direction[2]
    if t < 0.0:
        return None
    return Point2(ray.origin[0] + ray.direction[0] * t, ray.origin[1] + ray.direction[1] * t)


def point_to_segment_distance(
    point: Point2,
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """Distance from a 2D point to a segment (for proximity-based refinement)."""
    px, py = point.x, point.y
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-12:
        return float(np.hypot(px - sx, py - sy))
    t = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / length_squared))
    return float(np.hypot(px - (sx + t * dx), py - (sy + t * dy)))


__all__ = [
    "Ray3",
    "point_to_segment_distance",
    "project_point",
    "ray_plane_z",
    "unproject",
    "view_projection_matrix",
]
