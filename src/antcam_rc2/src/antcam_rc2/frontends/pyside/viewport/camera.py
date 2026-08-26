"""Orbit camera for the 3D viewport (pure numpy, no Qt dependency).

World coordinates follow the CAM convention directly: ``X`` right, ``Y``
forward on the table and ``Z`` up (the tool axis), so geometry, stock boxes and
toolpaths are rendered without any coordinate remapping.
"""

from __future__ import annotations

import math

import numpy as np

from antcam_rc2.core.rendering.picking import Ray3, unproject
from antcam_rc2.core.rendering.scene_graph import RenderBox


class OrbitCamera:
    """A target-centered orbit camera with yaw, pitch and distance controls."""

    def __init__(
        self,
        *,
        target: tuple[float, float, float] = (0.0, 0.0, 0.0),
        distance: float = 100.0,
        yaw_deg: float = -45.0,
        pitch_deg: float = 35.0,
        fov_deg: float = 45.0,
        near: float = 0.1,
        far: float = 100000.0,
    ) -> None:
        self.target = np.asarray(target, dtype=np.float64)
        self.distance = float(distance)
        self.yaw = math.radians(yaw_deg)
        self.pitch = math.radians(pitch_deg)
        self.fov = math.radians(fov_deg)
        self.near = near
        self.far = far

    @property
    def eye(self) -> np.ndarray:
        """The camera position in world space."""
        cp = math.cos(self.pitch)
        return self.target + self.distance * np.array(
            [cp * math.cos(self.yaw), cp * math.sin(self.yaw), math.sin(self.pitch)]
        )

    def view_matrix(self) -> np.ndarray:
        """Return the 4x4 view matrix (row-major, column-vector convention)."""
        eye = self.eye
        forward = self.target - eye
        forward /= np.linalg.norm(forward)
        up = np.array([0.0, 0.0, 1.0])
        right = np.cross(forward, up)
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

    def projection_matrix(self, aspect: float) -> np.ndarray:
        """Return a perspective projection matrix for the given aspect ratio."""
        f = 1.0 / math.tan(self.fov / 2.0)
        return np.array(
            [
                [f / aspect, 0.0, 0.0, 0.0],
                [0.0, f, 0.0, 0.0],
                [
                    0.0,
                    0.0,
                    (self.far + self.near) / (self.near - self.far),
                    (2.0 * self.far * self.near) / (self.near - self.far),
                ],
                [0.0, 0.0, -1.0, 0.0],
            ],
            dtype=np.float64,
        )

    def orbit(self, d_yaw_deg: float, d_pitch_deg: float) -> None:
        """Rotate the camera around the target (clamped pitch)."""
        self.yaw += math.radians(d_yaw_deg)
        self.pitch = min(math.pi / 2.0 - 0.01, max(-math.pi / 2.0 + 0.01, self.pitch + math.radians(d_pitch_deg)))

    def zoom(self, factor: float) -> None:
        """Multiply the orbit distance by ``factor`` (clamped to sane bounds)."""
        self.distance = min(1e6, max(1e-3, self.distance * factor))

    def pan_pixels(self, dx: float, dy: float, viewport_size: tuple[int, int]) -> None:
        """Pan the target on the camera plane for a pixel delta."""
        width, height = viewport_size
        if width <= 0 or height <= 0:
            return
        eye = self.eye
        forward = self.target - eye
        forward /= np.linalg.norm(forward)
        up = np.array([0.0, 0.0, 1.0])
        right = np.cross(forward, up)
        right /= np.linalg.norm(right)
        camera_up = np.cross(right, forward)
        world_per_pixel = 2.0 * self.distance * math.tan(self.fov / 2.0) / height
        self.target = self.target + right * (-dx * world_per_pixel) + camera_up * (dy * world_per_pixel)

    def fit_to(self, box: RenderBox, viewport_size: tuple[int, int] | None = None) -> None:
        """Center the camera on ``box`` and set the distance to frame it."""
        if box.is_empty:
            return
        self.target = np.asarray(box.center, dtype=np.float64)
        diagonal = math.sqrt(box.width**2 + box.height**2 + box.depth**2) or 1.0
        self.distance = diagonal / (2.0 * math.tan(self.fov / 2.0)) * 1.4 + diagonal * 0.1

    def screen_ray(self, screen_xy: tuple[float, float], viewport_size: tuple[int, int]) -> Ray3:
        """Cast a world-space ray through a screen pixel (top-left origin)."""
        return unproject(
            screen_xy,
            viewport_size,
            self.view_matrix(),
            self.projection_matrix(viewport_size[0] / max(1, viewport_size[1])),
        )
