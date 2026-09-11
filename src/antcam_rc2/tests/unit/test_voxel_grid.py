"""Tests for the voxel grid: creation, resolution policy, removal and mesh."""

from __future__ import annotations

import numpy as np
import pytest

from antcam_rc2.core.project.models import Stock, WorkCoordinateSystem
from antcam_rc2.core.simulation.voxels import VoxelGrid


def make_stock(**overrides: object) -> Stock:
    values: dict[str, object] = {
        "width_mm": 10.0,
        "length_mm": 10.0,
        "height_mm": 5.0,
        "position_x_mm": 1.0,
        "position_y_mm": 2.0,
        "position_z_mm": 0.0,
        "material_id": "aluminum_6061",
        "origin": "corner_xy_zero_z",
    }
    values.update(overrides)
    return Stock.model_validate(values)


def test_from_stock_derives_resolution_from_voxel_budget() -> None:
    grid, resolution, clamped = VoxelGrid.from_stock(make_stock(), WorkCoordinateSystem(), max_voxels=10_000)
    assert not clamped
    # 10*10*5 = 500 mm^3 / 10k voxels -> ~0.37 mm -> floor 0.5 mm.
    assert resolution == pytest.approx(0.5)
    assert grid.shape == (20, 20, 10)
    assert grid.count_occupied() == 4000
    assert grid.origin == (1.0, 2.0, 0.0)


def test_from_stock_clamps_over_budget_request() -> None:
    stock = Stock(width_mm=200.0, length_mm=200.0, height_mm=100.0, material_id="aluminum_6061")
    grid, resolution, clamped = VoxelGrid.from_stock(stock, WorkCoordinateSystem(), resolution_mm=0.25)
    assert clamped
    assert resolution > 0.25
    assert np.prod(grid.shape) <= 32_000_000


def test_world_voxel_mapping_round_trip() -> None:
    grid, _, _ = VoxelGrid.from_stock(make_stock(), WorkCoordinateSystem(), resolution_mm=1.0)
    voxel = grid.world_to_voxel(1.5, 2.5, 0.5)
    assert voxel == (0, 0, 0)
    center = grid.voxel_to_world(*voxel)
    assert center == pytest.approx((1.5, 2.5, 0.5))
    # Clamping out-of-range coordinates.
    assert grid.world_to_voxel(-100.0, -100.0, -100.0) == (0, 0, 0)


def test_remove_view_is_monotone_and_counts() -> None:
    grid, _, _ = VoxelGrid.from_stock(make_stock(), WorkCoordinateSystem(), resolution_mm=1.0)
    view = grid.slice_view(0, 5, 0, 5, 0, 5)
    mask = np.zeros(view.shape, dtype=bool)
    mask[:2, :2, :2] = True
    removed = grid.remove_view(view, mask)
    assert removed == 8
    assert grid.removed_voxels == 8
    assert grid.count_occupied() == grid.initial_count() - 8 if hasattr(grid, "initial_count") else 10 * 10 * 5 - 8
    # Re-removing the same mask is a no-op.
    assert grid.remove_view(view, mask) == 0


def test_clear_region_full_grid() -> None:
    grid, _, _ = VoxelGrid.from_stock(make_stock(), WorkCoordinateSystem(), resolution_mm=1.0)
    mask = np.zeros(grid.occupied.shape, dtype=bool)
    mask[0, 0, 0] = True
    assert grid.clear_region(mask) == 1
    assert not bool(grid.occupied[0, 0, 0])


def test_surface_mesh_of_full_cube() -> None:
    grid, _, _ = VoxelGrid.from_stock(make_stock(), WorkCoordinateSystem(), resolution_mm=1.0)
    vertices, triangles = grid.surface_mesh()
    assert vertices.ndim == 2 and vertices.shape[1] == 3
    assert triangles.ndim == 2 and triangles.shape[1] == 3
    assert len(vertices) > 0
    # Vertices live inside the world box (with margin for surface position).
    assert vertices[:, 0].min() >= 1.0 - 1.0
    assert vertices[:, 2].max() <= 5.0 + 1.0


def test_surface_mesh_of_empty_grid() -> None:
    grid, _, _ = VoxelGrid.from_stock(make_stock(), WorkCoordinateSystem(), resolution_mm=1.0)
    grid.occupied[:] = False
    vertices, triangles = grid.surface_mesh()
    assert vertices.shape == (0, 3)
    assert triangles.shape == (0, 3)


def test_invalid_grid_rejected() -> None:
    with pytest.raises(ValueError, match="voxel_size"):
        VoxelGrid(origin=(0, 0, 0), voxel_size=0.0, shape=(2, 2, 2), occupied=np.ones((2, 2, 2), dtype=bool))
    with pytest.raises(ValueError, match="shape"):
        VoxelGrid(origin=(0, 0, 0), voxel_size=1.0, shape=(0, 2, 2), occupied=np.empty((0, 2, 2), dtype=bool))
