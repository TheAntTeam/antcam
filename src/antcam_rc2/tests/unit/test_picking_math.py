"""Tests for the pure picking/viewport math."""

from __future__ import annotations

import numpy as np
import pytest

from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.rendering.picking import (
    Ray3,
    point_to_segment_distance,
    project_point,
    ray_plane_z,
    unproject,
)


def make_ortho_view() -> tuple[np.ndarray, np.ndarray]:
    """Simple orthographic camera: look from +Z down to the XY plane."""
    view = np.eye(4, dtype=np.float64)
    projection = np.diag([2.0 / 20.0, 2.0 / 20.0, 2.0 / 10.0, 1.0])
    return view, projection


def test_ray_rejects_zero_direction() -> None:
    with pytest.raises(ValueError, match="non-zero"):
        Ray3(origin=np.zeros(3), direction=np.zeros(3))


def test_unproject_and_plane_intersection_round_trip() -> None:
    view, projection = make_ortho_view()
    world = (5.0, 3.0, 2.0)
    screen = project_point(world, view, projection, (800, 600))
    ray = unproject(screen, (800, 600), view, projection)
    hit = ray_plane_z(ray, z=2.0)
    assert hit is not None
    assert hit.x == pytest.approx(5.0, abs=1e-6)
    assert hit.y == pytest.approx(3.0, abs=1e-6)


def test_ray_plane_parallel_returns_none() -> None:
    ray = Ray3(origin=np.array([0.0, 0.0, 5.0]), direction=np.array([1.0, 0.0, 0.0]))
    assert ray_plane_z(ray, z=2.0) is None


def test_ray_plane_behind_origin_returns_none() -> None:
    ray = Ray3(origin=np.array([0.0, 0.0, 0.0]), direction=np.array([0.0, 0.0, -1.0]))
    assert ray_plane_z(ray, z=5.0) is None


def test_project_point_center_of_screen() -> None:
    view, projection = make_ortho_view()
    screen = project_point((0.0, 0.0, 0.0), view, projection, (800, 600))
    assert screen == pytest.approx((400.0, 300.0))


def test_point_to_segment_distance() -> None:
    assert point_to_segment_distance(Point2(0.0, 0.0), (0.0, 0.0), (10.0, 0.0)) == pytest.approx(0.0)
    assert point_to_segment_distance(Point2(5.0, 3.0), (0.0, 0.0), (10.0, 0.0)) == pytest.approx(3.0)
    assert point_to_segment_distance(Point2(20.0, 0.0), (0.0, 0.0), (10.0, 0.0)) == pytest.approx(10.0)
    assert point_to_segment_distance(Point2(3.0, 4.0), (0.0, 0.0), (0.0, 0.0)) == pytest.approx(5.0)


def test_screen_coordinates_use_top_left_origin() -> None:
    view, projection = make_ortho_view()
    top = project_point((0.0, 10.0, 0.0), view, projection, (800, 600))
    bottom = project_point((0.0, -10.0, 0.0), view, projection, (800, 600))
    assert top[1] < bottom[1]
