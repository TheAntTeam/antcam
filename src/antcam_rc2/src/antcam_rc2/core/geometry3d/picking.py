"""CPU ray-mesh picking for 3D feature selection.

Uses a vectorized Möller–Trumbore intersection (numpy only, no extra
dependency) to map a world-space ray to a ``(body_index, feature_index)`` pair
via each body's triangle→feature map.
"""

from __future__ import annotations

import numpy as np

from antcam_rc2.core.geometry3d.scene import SolidScene

__all__ = ["ray_mesh_hit"]


def ray_mesh_hit(
    scene: SolidScene,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
) -> tuple[int, int] | None:
    """Return ``(body_index, feature_index)`` under the ray, or ``None``.

    The feature index is ``-1`` when the hit triangle does not belong to any
    detected feature (e.g. a non-planar wall).
    """
    ray_origin = np.asarray(origin, dtype=np.float64)
    ray_direction = np.asarray(direction, dtype=np.float64)
    best_distance: float | None = None
    best_hit: tuple[int, int] | None = None
    for body_index, body in enumerate(scene.bodies):
        hit_triangle, distance = _ray_intersect(body.mesh.vertices, body.mesh.faces, ray_origin, ray_direction)
        if hit_triangle is None:
            continue
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_hit = (body_index, body.triangle_feature_map().get(hit_triangle, -1))
    return best_hit


def _ray_intersect(
    vertices: np.ndarray, faces: np.ndarray, origin: np.ndarray, direction: np.ndarray
) -> tuple[int | None, float]:
    """Return ``(triangle_index, distance)`` of the closest hit, or ``(None, 0)``."""
    if len(faces) == 0:
        return None, 0.0
    v0 = vertices[faces[:, 0]]
    v1 = vertices[faces[:, 1]]
    v2 = vertices[faces[:, 2]]
    edge1 = v1 - v0
    edge2 = v2 - v0
    direction_b = np.broadcast_to(direction, edge1.shape)
    pvec = np.cross(direction_b, edge2)
    det = np.einsum("ij,ij->i", edge1, pvec)
    parallel = np.abs(det) < 1e-12
    inv_det = np.zeros_like(det)
    inv_det[~parallel] = 1.0 / det[~parallel]
    tvec = origin - v0
    u = np.einsum("ij,ij->i", tvec, pvec) * inv_det
    qvec = np.cross(tvec, edge1)
    v = np.einsum("ij,ij->i", direction_b, qvec) * inv_det
    t = np.einsum("ij,ij->i", edge2, qvec) * inv_det
    valid = (~parallel) & (u >= 0.0) & (v >= 0.0) & (u + v <= 1.0) & (t >= 0.0)
    if not valid.any():
        return None, 0.0
    candidates = np.where(valid)[0]
    distances = t[candidates]
    nearest = int(np.argmin(distances))
    return int(candidates[nearest]), float(distances[nearest])
