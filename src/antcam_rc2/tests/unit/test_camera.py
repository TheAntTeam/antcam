"""Tests for the pure orbit camera math."""

from __future__ import annotations

import numpy as np
import pytest

from antcam_rc2.core.rendering.picking import project_point, ray_plane_z
from antcam_rc2.core.rendering.scene_graph import RenderBox
from antcam_rc2.frontends.pyside.viewport.camera import OrbitCamera


def test_view_matrix_translates_origin_to_eye_distance() -> None:
    camera = OrbitCamera(target=(0.0, 0.0, 0.0), distance=10.0, yaw_deg=0.0, pitch_deg=0.0)
    view = camera.view_matrix()
    # Looking along +X from (10, 0, 0); the origin maps to -Z (in front).
    transformed = view @ np.array([0.0, 0.0, 0.0, 1.0])
    assert transformed[2] == pytest.approx(-10.0, abs=1e-6)


def test_projection_keeps_center_point_on_screen() -> None:
    camera = OrbitCamera(target=(0.0, 0.0, 0.0), distance=10.0)
    screen = project_point((0.0, 0.0, 0.0), camera.view_matrix(), camera.projection_matrix(1.0), (800, 600))
    assert screen == pytest.approx((400.0, 300.0))


def test_screen_ray_hits_target_plane_center() -> None:
    camera = OrbitCamera(target=(0.0, 0.0, 0.0), distance=10.0, yaw_deg=30.0, pitch_deg=25.0)
    ray = camera.screen_ray((400, 300), (800, 600))
    hit = ray_plane_z(ray, z=0.0)
    assert hit is not None
    assert hit.x == pytest.approx(0.0, abs=1e-4)
    assert hit.y == pytest.approx(0.0, abs=1e-4)


def test_zoom_clamps_and_orbits_clamp_pitch() -> None:
    camera = OrbitCamera(distance=10.0, pitch_deg=0.0)
    camera.zoom(0.5)
    assert camera.distance == pytest.approx(5.0)
    camera.zoom(1e9)
    assert camera.distance <= 1e6
    camera.orbit(0.0, 200.0)
    assert camera.pitch < np.pi / 2.0
    camera.orbit(0.0, -200.0)
    assert camera.pitch > -np.pi / 2.0


def test_pan_moves_target_on_camera_plane() -> None:
    camera = OrbitCamera(target=(5.0, 5.0, 0.0), distance=100.0, fov_deg=45.0)
    before = camera.target.copy()
    camera.pan_pixels(50.0, 0.0, (800, 600))
    delta = np.linalg.norm(camera.target - before)
    assert delta > 1.0  # moved significantly


def test_fit_to_centers_and_frames_box() -> None:
    camera = OrbitCamera()
    box = RenderBox(min_x=0.0, min_y=0.0, min_z=0.0, max_x=20.0, max_y=20.0, max_z=5.0)
    camera.fit_to(box)
    assert camera.target == pytest.approx((10.0, 10.0, 2.5))
    assert camera.distance > 20.0  # large enough to frame the diagonal


def test_fit_to_ignores_empty_box() -> None:
    camera = OrbitCamera(distance=50.0)
    camera.fit_to(RenderBox.empty())
    assert camera.distance == pytest.approx(50.0)
