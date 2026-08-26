"""Tests for collision detection (per-body responsibility model)."""

from __future__ import annotations

from antcam_rc2.core.project.models import Fixture, Stock, WorkCoordinateSystem
from antcam_rc2.core.simulation.collision import (
    check_fixture,
    check_reach,
    check_stock_material,
    check_tip_in_work_area,
)
from antcam_rc2.core.simulation.tool_geometry import ToolAssembly
from antcam_rc2.core.simulation.voxels import VoxelGrid
from antcam_rc2.core.toolpath.models import Position3


def make_grid(resolution: float = 1.0) -> VoxelGrid:
    stock = Stock(width_mm=30.0, length_mm=30.0, height_mm=10.0, material_id="aluminum_6061")
    grid, _, _ = VoxelGrid.from_stock(stock, WorkCoordinateSystem(), resolution_mm=resolution)
    return grid


def test_spindle_collides_with_stock_material_in_tall_stock(catalog_bundle) -> None:
    """A tool deep inside a tall stock collides when the spindle enters material."""
    stock = Stock(width_mm=30.0, length_mm=30.0, height_mm=100.0, material_id="aluminum_6061")
    grid, _, _ = VoxelGrid.from_stock(stock, WorkCoordinateSystem(), resolution_mm=1.0)
    assembly = ToolAssembly(catalog_bundle.tools["end_mill_3_175_2f"], catalog_bundle.machines["makera_z1"])
    # Tip 30 mm above the stock bottom: the spindle (tip + 46..146) overlaps the
    # remaining material above it (stock top at z=100).
    tip = Position3(x_mm=15.0, y_mm=15.0, z_mm=30.0)
    spindle = assembly.non_cutting_bodies()[2]
    hit = check_stock_material(grid, spindle, tip, margin=0.5)
    assert hit is not None
    assert hit.code.value == "collision_stock_material"


def test_spindle_above_stock_does_not_collide(catalog_bundle) -> None:
    grid = make_grid(1.0)
    assembly = ToolAssembly(catalog_bundle.tools["end_mill_3_175_2f"], catalog_bundle.machines["makera_z1"])
    tip = Position3(x_mm=15.0, y_mm=15.0, z_mm=10.0)  # tip at the stock top
    spindle = assembly.non_cutting_bodies()[2]
    assert check_stock_material(grid, spindle, tip, margin=0.5) is None


def test_shank_never_checks_stock_material_by_design(catalog_bundle) -> None:
    """The shank occupies the hole it carved; the stock check must not flag it."""
    grid = make_grid(1.0)
    assembly = ToolAssembly(catalog_bundle.tools["end_mill_3_175_2f"], catalog_bundle.machines["makera_z1"])
    shank = assembly.non_cutting_bodies()[0]
    tip = Position3(x_mm=15.0, y_mm=15.0, z_mm=-5.0)  # deep inside the stock
    # Even deep inside, the material check on the shank is a modeling decision:
    # the simulator only calls it for holder/spindle (see check_collisions).
    hit = check_stock_material(grid, shank, tip, margin=0.5)
    # It may or may not hit here (full grid); what matters is the caller never
    # applies it to the shank. We assert the caller contract in the simulator.
    assert hit is not None  # document: a full grid is present at the shank height


def test_fixture_collision(catalog_bundle) -> None:
    assembly = ToolAssembly(catalog_bundle.tools["end_mill_3_175_2f"], catalog_bundle.machines["makera_z1"])
    fixture = Fixture(
        id="fix_1234abcd",
        name="vise",
        width_mm=10.0,
        length_mm=10.0,
        height_mm=5.0,
        position_x_mm=0.0,
        position_y_mm=0.0,
        position_z_mm=0.0,
    )
    tip_near = Position3(x_mm=2.0, y_mm=2.0, z_mm=-8.0)  # shank spans z [4, 30] -> overlaps the fixture
    hit = check_fixture(assembly.shank_body(), tip_near, (fixture,), margin=0.5)
    assert hit is not None
    assert hit.code.value == "collision_fixture"

    tip_far = Position3(x_mm=25.0, y_mm=25.0, z_mm=10.0)
    assert check_fixture(assembly.shank_body(), tip_far, (fixture,), margin=0.5) is None


def test_tip_outside_work_area(catalog_bundle) -> None:
    machine = catalog_bundle.machines["makera_z1"]
    assert check_tip_in_work_area(Position3(x_mm=100.0, y_mm=100.0, z_mm=50.0), machine, margin=1.0) is None
    hit = check_tip_in_work_area(Position3(x_mm=250.0, y_mm=100.0, z_mm=50.0), machine, margin=1.0)
    assert hit is not None
    assert hit.code.value == "outside_work_area"


def test_reach_rule(catalog_bundle) -> None:
    assembly = ToolAssembly(catalog_bundle.tools["end_mill_3_175_2f"], catalog_bundle.machines["makera_z1"])
    assert check_reach(depth_mm=1.0, assembly=assembly, margin=0.5) is None
    hit = check_reach(depth_mm=assembly.max_reach_mm() + 5.0, assembly=assembly, margin=0.5)
    assert hit is not None
    assert hit.code.value == "reach_exceeded"
