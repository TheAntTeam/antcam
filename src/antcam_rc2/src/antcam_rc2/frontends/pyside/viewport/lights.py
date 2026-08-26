"""Lighting definitions and directional-light shadow matrices (pure numpy)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from antcam_rc2.core.rendering.scene_graph import RenderBox


@dataclass(frozen=True, slots=True)
class Lights:
    """One directional light (with shadow map), one point light and ambient."""

    light_dir: tuple[float, float, float] = (-0.5, -0.7, 0.6)
    light_color: tuple[float, float, float] = (1.0, 0.98, 0.92)
    ambient: tuple[float, float, float] = (0.25, 0.26, 0.3)
    point_pos: tuple[float, float, float] = (0.0, 0.0, 60.0)
    point_color: tuple[float, float, float] = (0.4, 0.45, 0.55)
    point_radius: float = 400.0

    @property
    def normalized_light_dir(self) -> np.ndarray:
        """Unit vector pointing in the direction the light shines."""
        vector = np.asarray(self.light_dir, dtype=np.float64)
        return vector / np.linalg.norm(vector)


def look_at(eye: np.ndarray, center: np.ndarray, up: np.ndarray) -> np.ndarray:
    """A Z-up look-at view matrix (same convention as the orbit camera).

    Falls back to a different up vector when the eye is (near-)aligned with the
    requested up axis (degenerate ``cross(forward, up)``).
    """
    forward = center - eye
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up)
    if np.linalg.norm(right) <= 1e-9:
        up = np.array([0.0, 1.0, 0.0])
        right = np.cross(forward, up)
        right /= np.linalg.norm(right)
    else:
        right /= np.linalg.norm(right)
    camera_up = np.cross(right, forward)
    return np.array(
        [
            [right[0], right[1], right[2], -np.dot(right, eye)],
            [camera_up[0], camera_up[1], camera_up[2], -np.dot(camera_up, eye)],
            [-forward[0], -forward[1], -forward[2], np.dot(forward, eye)],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def orthographic_projection(left, right, bottom, top, near, far) -> np.ndarray:
    """An orthographic projection matrix."""
    return np.array(
        [
            [2.0 / (right - left), 0.0, 0.0, -(right + left) / (right - left)],
            [0.0, 2.0 / (top - bottom), 0.0, -(top + bottom) / (top - bottom)],
            [0.0, 0.0, -2.0 / (far - near), -(far + near) / (far - near)],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def directional_light_matrices(
    lights: Lights,
    scene_box: RenderBox,
    margin: float = 10.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(view, projection)`` framing the scene from the light's side.

    The light is placed ``radius`` units away along the reversed light
    direction; the orthographic frustum covers the scene bounding box.
    """
    center = np.asarray(scene_box.center, dtype=np.float64)
    radius = (math.sqrt(scene_box.width**2 + scene_box.height**2 + scene_box.depth**2) / 2.0 + margin) or margin
    direction = lights.normalized_light_dir
    eye = center - direction * radius
    view = look_at(eye, center, np.array([0.0, 0.0, 1.0]))
    projection = orthographic_projection(-radius, radius, -radius, radius, 0.0, 2.0 * radius)
    return view, projection


__all__ = [
    "Lights",
    "directional_light_matrices",
    "look_at",
    "orthographic_projection",
]
