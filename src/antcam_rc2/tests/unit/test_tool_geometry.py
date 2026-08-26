"""Tests for the tool assembly geometry (stack and per-shape radii)."""

from __future__ import annotations

import pytest

from antcam_rc2.core.simulation.tool_geometry import BodyShape, ToolAssembly


def test_cylinder_radius_is_constant(catalog_bundle) -> None:
    tool = catalog_bundle.tools["end_mill_3_175_2f"]
    machine = catalog_bundle.machines["makera_z1"]
    assembly = ToolAssembly(tool, machine)
    cutter = assembly.cutter_body()
    assert cutter.is_cutting
    assert cutter.z_offset_max == pytest.approx(tool.flute_length_mm)
    assert cutter.radius_at(0.0) == pytest.approx(tool.cutting_diameter_mm / 2)
    assert cutter.radius_at(tool.flute_length_mm) == pytest.approx(tool.cutting_diameter_mm / 2)


def test_ball_cap_radius_profile(catalog_bundle) -> None:
    tool = catalog_bundle.tools["ball_mill_3_175_2f"]
    machine = catalog_bundle.machines["makera_z1"]
    cutter = ToolAssembly(tool, machine).cutter_body()
    radius = tool.cutting_diameter_mm / 2
    assert cutter.shape is BodyShape.BALL_CAP
    assert cutter.radius_at(0.0) == pytest.approx(0.0)
    assert cutter.radius_at(radius) == pytest.approx(radius)
    assert cutter.radius_at(tool.flute_length_mm) == pytest.approx(radius)


def test_v_bit_cone_radius_grows_with_height(catalog_bundle) -> None:
    tool = catalog_bundle.tools["v_bit_30"]
    machine = catalog_bundle.machines["makera_z1"]
    cutter = ToolAssembly(tool, machine).cutter_body()
    assert cutter.shape is BodyShape.CONE
    assert cutter.radius_at(0.0) == pytest.approx(0.0)
    # At the top of the flute the cone reaches the cutting radius.
    assert cutter.radius_at(tool.flute_length_mm) == pytest.approx(tool.cutting_diameter_mm / 2)
    halfway = cutter.radius_at(tool.flute_length_mm / 2)
    assert halfway == pytest.approx(tool.cutting_diameter_mm / 4)


def test_v_bit_without_angle_falls_back_to_cylinder(catalog_bundle) -> None:
    tool = catalog_bundle.tools["v_bit_30"].model_copy(update={"v_bit_angle_deg": None})
    machine = catalog_bundle.machines["makera_z1"]
    assembly = ToolAssembly(tool, machine)
    assert assembly.cutter_body().shape is BodyShape.CYLINDER
    assert assembly.unknown_geometry_warning() is not None
    assert ToolAssembly(catalog_bundle.tools["v_bit_30"], machine).unknown_geometry_warning() is None


def test_t_slot_neck_and_head(catalog_bundle) -> None:
    tool = catalog_bundle.tools["t_slot_6"]
    machine = catalog_bundle.machines["makera_z1"]
    assembly = ToolAssembly(tool, machine)
    cutter = assembly.cutter_body()
    assert cutter.radius == pytest.approx(tool.t_slot_head_diameter_mm / 2)
    shank = assembly.shank_body()
    assert shank.radius == pytest.approx(tool.t_slot_neck_diameter_mm / 2)
    assert assembly.unknown_geometry_warning() is None


def test_non_cutting_stack_heights(catalog_bundle) -> None:
    tool = catalog_bundle.tools["end_mill_3_175_2f"]
    machine = catalog_bundle.machines["makera_z1"]
    assembly = ToolAssembly(tool, machine)
    bodies = assembly.non_cutting_bodies()
    assert [body.name for body in bodies] == ["shank", "collet_holder", "spindle"]
    shank, holder, spindle = bodies
    assert shank.z_offset_min == pytest.approx(tool.flute_length_mm)
    assert shank.z_offset_max == pytest.approx(tool.overall_length_mm)
    assert holder.z_offset_min == pytest.approx(tool.overall_length_mm)
    assert spindle.z_offset_max == pytest.approx(
        tool.overall_length_mm + assembly.collet_holder_length_mm + machine.spindle_length_mm
    )
    assert holder.radius == pytest.approx(machine.spindle_diameter_mm / 2)
    assert all(not body.is_cutting for body in bodies)


def test_max_reach_is_flute_length(catalog_bundle) -> None:
    tool = catalog_bundle.tools["end_mill_3_175_2f"]
    assembly = ToolAssembly(tool, catalog_bundle.machines["makera_z1"])
    assert assembly.max_reach_mm() == pytest.approx(tool.flute_length_mm)
