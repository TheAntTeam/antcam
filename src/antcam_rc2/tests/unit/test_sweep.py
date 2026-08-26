"""Tests for the vectorized swept-volume removal."""

from __future__ import annotations

from antcam_rc2.core.project.models import Stock, WorkCoordinateSystem
from antcam_rc2.core.simulation.sweep import remove_motion, remove_segment
from antcam_rc2.core.simulation.tool_geometry import ToolAssembly
from antcam_rc2.core.simulation.voxels import VoxelGrid
from antcam_rc2.core.toolpath.models import MotionCommand, MotionKind, Position3


def make_grid(resolution: float = 1.0) -> VoxelGrid:
    stock = Stock(width_mm=30.0, length_mm=30.0, height_mm=10.0, material_id="aluminum_6061")
    grid, _, _ = VoxelGrid.from_stock(stock, WorkCoordinateSystem(), resolution_mm=resolution)
    return grid


def test_remove_segment_removes_capsule_volume() -> None:
    grid = make_grid(1.0)
    initial = grid.count_occupied()
    p1 = Position3(x_mm=5.0, y_mm=5.0, z_mm=0.0)
    p2 = Position3(x_mm=25.0, y_mm=5.0, z_mm=0.0)
    removed = remove_segment(grid, p1, p2, radius=2.0, z_min=0.0, z_max=2.0)
    assert removed > 0
    # A 20 x 4 x 2 mm box minus the rounded ends, at 1 mm resolution.
    expected_box = 20 * 4 * 2
    assert expected_box - 40 <= removed <= expected_box + 40
    assert grid.count_occupied() == initial - removed


def test_remove_segment_with_z_variation() -> None:
    grid = make_grid(1.0)
    p1 = Position3(x_mm=5.0, y_mm=5.0, z_mm=4.0)
    p2 = Position3(x_mm=5.0, y_mm=5.0, z_mm=0.0)  # vertical plunge
    removed = remove_segment(grid, p1, p2, radius=1.0, z_min=0.0, z_max=4.0)
    assert removed > 0


def test_remove_motion_linear_and_arc(catalog_bundle) -> None:
    grid = make_grid(1.0)
    assembly = ToolAssembly(catalog_bundle.tools["end_mill_3_175_2f"], catalog_bundle.machines["makera_z1"])
    start = Position3(x_mm=5.0, y_mm=5.0, z_mm=0.0)
    linear = MotionCommand(
        kind=MotionKind.CUT_LINEAR,
        endpoint=Position3(x_mm=20.0, y_mm=5.0, z_mm=0.0),
        feed_mm_min=100.0,
    )
    before = grid.count_occupied()
    removed_linear = remove_motion(grid, start, linear, assembly.cutter_body(), 1.0)
    assert removed_linear > 0

    arc = MotionCommand(
        kind=MotionKind.CUT_ARC_CCW,
        endpoint=Position3(x_mm=20.0, y_mm=5.0, z_mm=0.0),  # full circle
        feed_mm_min=100.0,
        arc_center_xy=(12.5, 5.0),
    )
    removed_arc = remove_motion(grid, Position3(x_mm=20.0, y_mm=5.0, z_mm=0.0), arc, assembly.cutter_body(), 1.0)
    assert removed_arc > removed_linear
    assert grid.count_occupied() == before - removed_linear - removed_arc


def test_remove_motion_helix_removes_more_than_flat(catalog_bundle) -> None:
    grid = make_grid(1.0)
    assembly = ToolAssembly(catalog_bundle.tools["end_mill_3_175_2f"], catalog_bundle.machines["makera_z1"])
    start = Position3(x_mm=10.0, y_mm=10.0, z_mm=0.0)
    flat = MotionCommand(
        kind=MotionKind.CUT_ARC_CCW,
        endpoint=Position3(x_mm=10.0, y_mm=10.0, z_mm=0.0),
        feed_mm_min=100.0,
        arc_center_xy=(10.0, 10.0),
    )
    helix = flat.model_copy(update={"endpoint": Position3(x_mm=10.0, y_mm=10.0, z_mm=-3.0)})
    flat_removed = remove_motion(grid, start, flat, assembly.cutter_body(), 1.0)
    helix_removed = remove_motion(grid, start, helix, assembly.cutter_body(), 1.0)
    assert helix_removed > flat_removed


def test_rapid_does_not_remove(catalog_bundle) -> None:
    grid = make_grid(1.0)
    assembly = ToolAssembly(catalog_bundle.tools["end_mill_3_175_2f"], catalog_bundle.machines["makera_z1"])
    before = grid.count_occupied()
    rapid = MotionCommand(kind=MotionKind.RAPID, endpoint=Position3(x_mm=20.0, y_mm=20.0, z_mm=5.0))
    removed = remove_motion(grid, Position3(x_mm=0.0, y_mm=0.0, z_mm=5.0), rapid, assembly.cutter_body(), 1.0)
    assert removed == 0
    assert grid.count_occupied() == before


def test_determinism_of_sweep(catalog_bundle) -> None:
    assembly = ToolAssembly(catalog_bundle.tools["end_mill_3_175_2f"], catalog_bundle.machines["makera_z1"])
    results: set[int] = set()
    for _ in range(5):
        grid = make_grid(1.0)
        motion = MotionCommand(
            kind=MotionKind.CUT_LINEAR,
            endpoint=Position3(x_mm=20.0, y_mm=10.0, z_mm=0.0),
            feed_mm_min=100.0,
        )
        results.add(remove_motion(grid, Position3(x_mm=5.0, y_mm=10.0, z_mm=0.0), motion, assembly.cutter_body(), 1.0))
    assert len(results) == 1


def test_cone_body_removes_per_slice(catalog_bundle) -> None:
    """A V-bit cone removes less at the tip and more at the top (per-slice radii)."""
    from antcam_rc2.core.simulation.sweep import remove_body_segment

    grid = make_grid(0.5)
    assembly = ToolAssembly(catalog_bundle.tools["v_bit_30"], catalog_bundle.machines["makera_z1"])
    body = assembly.cutter_body()
    start = Position3(x_mm=15.0, y_mm=15.0, z_mm=0.0)
    end = Position3(x_mm=15.0, y_mm=15.0, z_mm=0.0)
    removed = remove_body_segment(grid, start, end, body, tip_z=0.0)
    assert removed > 0
    # The deepest slice (top of the cone) must remove at least as many voxels
    # as the shallowest (tip) slice: count occupied after in the top vs bottom.
    top_slice = int(grid.occupied[:, :, -1].sum())
    bottom_slice = int(grid.occupied[:, :, 0].sum())
    assert grid.occupied.shape[2] > 2
    # The cone removes more at the wide top than at the sharp tip.
    assert top_slice <= bottom_slice


def test_ball_body_removes_with_cap(catalog_bundle) -> None:
    from antcam_rc2.core.simulation.sweep import remove_body_segment

    grid = make_grid(0.5)
    assembly = ToolAssembly(catalog_bundle.tools["ball_mill_3_175_2f"], catalog_bundle.machines["makera_z1"])
    start = Position3(x_mm=15.0, y_mm=15.0, z_mm=0.0)
    removed = remove_body_segment(grid, start, start, assembly.cutter_body(), tip_z=0.0)
    assert removed > 0
    # The cap slice at the tip removes fewer voxels than the full-radius slice above.
    assert int(grid.occupied[:, :, 0].sum()) >= int(grid.occupied[:, :, 3].sum())


def test_degenerate_arc_returns_to_linear(catalog_bundle) -> None:
    """An arc with a zero-radius center degenerates to the endpoints."""
    grid = make_grid(1.0)
    assembly = ToolAssembly(catalog_bundle.tools["end_mill_3_175_2f"], catalog_bundle.machines["makera_z1"])
    start = Position3(x_mm=5.0, y_mm=5.0, z_mm=0.0)
    degenerate = MotionCommand(
        kind=MotionKind.CUT_ARC_CCW,
        endpoint=Position3(x_mm=10.0, y_mm=5.0, z_mm=0.0),
        feed_mm_min=100.0,
        arc_center_xy=(5.0, 5.0),
    )
    removed = remove_motion(grid, start, degenerate, assembly.cutter_body(), 1.0)
    assert removed > 0


def test_remove_segment_guards(catalog_bundle) -> None:
    from antcam_rc2.core.simulation.sweep import remove_segment

    grid = make_grid(1.0)
    p = Position3(x_mm=5.0, y_mm=5.0, z_mm=0.0)
    assert remove_segment(grid, p, p, radius=0.0, z_min=0.0, z_max=2.0) == 0
    assert remove_segment(grid, p, p, radius=1.0, z_min=3.0, z_max=2.0) == 0


def test_cone_motion_via_generic_path(catalog_bundle) -> None:
    """A V-bit linear motion goes through the generic subdivide+slice path."""
    grid = make_grid(0.5)
    assembly = ToolAssembly(catalog_bundle.tools["v_bit_30"], catalog_bundle.machines["makera_z1"])
    start = Position3(x_mm=10.0, y_mm=10.0, z_mm=0.0)
    motion = MotionCommand(
        kind=MotionKind.CUT_LINEAR,
        endpoint=Position3(x_mm=20.0, y_mm=10.0, z_mm=0.0),
        feed_mm_min=100.0,
    )
    removed = remove_motion(grid, start, motion, assembly.cutter_body(), 0.5)
    assert removed > 0
    # A shallow trench is wider at the top than at the tip.
    assert int(grid.occupied[:, :, -1].sum()) < int(grid.occupied[:, :, 0].sum())
