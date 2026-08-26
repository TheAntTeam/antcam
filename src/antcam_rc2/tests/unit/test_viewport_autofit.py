"""Tests for viewport auto-fit behavior on scene changes."""

from __future__ import annotations

import numpy as np
import pytest

from antcam_rc2.frontends.pyside.viewport.camera import OrbitCamera
from antcam_rc2.core.rendering.scene_graph import RenderBox


class TestViewportAutoFit:
    """Test that the viewport auto-fits when the scene changes."""

    def test_initial_camera_position_shows_origin_area(self):
        """Test that the default camera only shows the origin area."""
        camera = OrbitCamera()  # Default: target=(0,0,0), distance=100, yaw=-45, pitch=35

        # A scene box representing a 100x100x20 stock at origin
        scene_box = RenderBox(min_x=0.0, min_y=0.0, min_z=0.0, max_x=100.0, max_y=100.0, max_z=20.0)

        viewport_size = (1280, 860)
        view = camera.view_matrix()
        proj = camera.projection_matrix(viewport_size[0] / viewport_size[1])
        vp = proj @ view

        # Check corners
        corners = [
            (scene_box.min_x, scene_box.min_y, scene_box.min_z),
            (scene_box.max_x, scene_box.min_y, scene_box.min_z),
            (scene_box.min_x, scene_box.max_y, scene_box.min_z),
            (scene_box.max_x, scene_box.max_y, scene_box.min_z),
        ]

        visible_count = 0
        for x, y, z in corners:
            p = vp @ np.array([x, y, z, 1.0], dtype=np.float64)
            if abs(p[3]) > 1e-9:
                ndc = p[:3] / p[3]
                if -1.0 <= ndc[0] <= 1.0 and -1.0 <= ndc[1] <= 1.0 and -1.0 <= ndc[2] <= 1.0:
                    visible_count += 1

        # With default camera, only corners near origin (1-2) should be visible
        assert visible_count <= 2, f"Expected at most 2 visible corners with default camera, got {visible_count}"

    def test_fitted_camera_shows_full_scene(self):
        """Test that a fitted camera shows the entire scene."""
        camera = OrbitCamera()
        scene_box = RenderBox(min_x=0.0, min_y=0.0, min_z=0.0, max_x=100.0, max_y=100.0, max_z=20.0)

        viewport_size = (1280, 860)
        camera.fit_to(scene_box, viewport_size)

        view = camera.view_matrix()
        proj = camera.projection_matrix(viewport_size[0] / viewport_size[1])
        vp = proj @ view

        corners = [
            (scene_box.min_x, scene_box.min_y, scene_box.min_z),
            (scene_box.max_x, scene_box.min_y, scene_box.min_z),
            (scene_box.min_x, scene_box.max_y, scene_box.min_z),
            (scene_box.max_x, scene_box.max_y, scene_box.min_z),
            (scene_box.min_x, scene_box.min_y, scene_box.max_z),
            (scene_box.max_x, scene_box.min_y, scene_box.max_z),
            (scene_box.min_x, scene_box.max_y, scene_box.max_z),
            (scene_box.max_x, scene_box.max_y, scene_box.max_z),
        ]

        visible_count = 0
        for x, y, z in corners:
            p = vp @ np.array([x, y, z, 1.0], dtype=np.float64)
            if abs(p[3]) > 1e-9:
                ndc = p[:3] / p[3]
                if -1.0 <= ndc[0] <= 1.0 and -1.0 <= ndc[1] <= 1.0 and -1.0 <= ndc[2] <= 1.0:
                    visible_count += 1

        # With fitted camera, at least 6 of 8 corners should be visible
        assert visible_count >= 6, f"Expected at least 6 visible corners with fitted camera, got {visible_count}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])