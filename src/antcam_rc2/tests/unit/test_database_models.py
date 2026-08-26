"""Tests for validated, serializable catalog domain models."""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import ValidationError

from antcam_rc2.core.databases.models import (
    CatalogEnvelope,
    CoolingKind,
    CoolingProfile,
    DataStatus,
    MachineProfile,
    MaterialProfile,
    Tool,
    ToolType,
)


def make_machine(**overrides: object) -> MachineProfile:
    values: dict[str, object] = {
        "id": "makera_z1",
        "name": "Makera Z1",
        "vendor": "Makera",
        "model": "Z1",
        "catalog_version": "1.0",
        "source_reference": "manufacturer documentation",
        "data_status": DataStatus.VERIFIED,
        "work_area_x_mm": 200.0,
        "work_area_y_mm": 200.0,
        "work_area_z_mm": 100.0,
        "spindle_power_w": 150.0,
        "min_rpm": 0.0,
        "max_rpm": 13000.0,
        "max_feed_mm_min": 1000.0,
        "collet_sizes_mm": (3.175, 4.0, 6.0, 6.35),
        "default_collet_size_mm": 3.175,
        "supported_cooling_ids": ("aerodust",),
        "default_cooling_id": "aerodust",
        "native_post": "makera",
        "spindle_diameter_mm": 52.0,
        "spindle_length_mm": 100.0,
    }
    values.update(overrides)
    return MachineProfile.model_validate(values)


def test_machine_profile_is_immutable_and_json_round_trips() -> None:
    machine = make_machine()

    with pytest.raises(ValidationError):
        cast(Any, machine).max_rpm = 12000.0

    restored = MachineProfile.model_validate_json(machine.model_dump_json())
    assert restored == machine


@pytest.mark.parametrize("invalid_id", ["Makera_Z1", "makera-z1", "1makera", "makera__z1"])
def test_catalog_ids_must_be_snake_case(invalid_id: str) -> None:
    with pytest.raises(ValidationError, match="snake_case"):
        make_machine(id=invalid_id)


def test_machine_profile_requires_coherent_spindle_collet_and_cooling_values() -> None:
    with pytest.raises(ValidationError, match="max_rpm"):
        make_machine(min_rpm=13000.0, max_rpm=13000.0)

    with pytest.raises(ValidationError, match="default_collet_size_mm"):
        make_machine(default_collet_size_mm=5.0)

    with pytest.raises(ValidationError, match="default_cooling_id"):
        make_machine(default_cooling_id="flood")


def test_machine_capability_flags_default_to_disabled_and_round_trip() -> None:
    machine = make_machine()
    assert machine.rigid_tapping is False
    assert machine.three_d_toolpath is False

    rigid = make_machine(rigid_tapping=True, three_d_toolpath=True)
    assert rigid.rigid_tapping is True
    assert rigid.three_d_toolpath is True
    restored = MachineProfile.model_validate_json(rigid.model_dump_json())
    assert restored.rigid_tapping is True


def test_tool_shape_geometry_fields_are_optional_and_validated() -> None:
    from antcam_rc2.core.databases.models import Tool

    tool = Tool(
        id="v_bit_30",
        name="V-bit",
        source_reference="test",
        tool_type=ToolType.V_BIT,
        cutting_diameter_mm=3.0,
        shank_diameter_mm=3.0,
        flute_count=1,
        flute_length_mm=12.0,
        overall_length_mm=38.0,
        tool_material="carbide",
        v_bit_angle_deg=30.0,
    )
    assert tool.v_bit_angle_deg == 30.0
    assert tool.t_slot_head_diameter_mm is None  # optional by default

    with pytest.raises(ValidationError, match="angle"):
        Tool.model_validate({**tool.model_dump(), "v_bit_angle_deg": 0.0})
    with pytest.raises(ValidationError, match="t_slot_head"):
        Tool.model_validate(
            {
                **tool.model_dump(),
                "t_slot_neck_diameter_mm": 5.0,
                "t_slot_head_diameter_mm": 3.0,
            }
        )


def test_tool_material_and_cooling_profiles_validate_physical_ranges() -> None:
    tool = Tool(
        id="end_mill_3_175_2f",
        name="3.175 mm end mill",
        source_reference="tool manufacturer documentation",
        tool_type=ToolType.END_MILL,
        cutting_diameter_mm=3.175,
        shank_diameter_mm=3.175,
        flute_count=2,
        flute_length_mm=12.0,
        overall_length_mm=38.0,
        tool_material="carbide",
    )
    material = MaterialProfile(
        id="aluminum_6061",
        name="Aluminum 6061",
        source_reference="material machining handbook",
        family="aluminum",
        surface_speed_m_min=250.0,
        chip_load_mm_tooth=0.025,
        plunge_ratio=0.4,
        machinability_factor=1.0,
    )
    cooling = CoolingProfile(
        id="aerodust",
        name="AeroDust",
        source_reference="machine manufacturer documentation",
        kind=CoolingKind.AIR,
        surface_speed_factor=1.0,
        chip_load_factor=1.0,
    )

    assert tool.tool_type is ToolType.END_MILL
    assert material.plunge_ratio == pytest.approx(0.4)
    assert cooling.kind is CoolingKind.AIR

    with pytest.raises(ValidationError):
        Tool(
            id="bad_tool",
            name="Bad tool",
            source_reference="test fixture",
            tool_type=ToolType.END_MILL,
            cutting_diameter_mm=4.0,
            shank_diameter_mm=3.0,
            flute_count=2,
            flute_length_mm=12.0,
            overall_length_mm=10.0,
            tool_material="carbide",
        )

    with pytest.raises(ValidationError):
        MaterialProfile(
            id="bad_material",
            name="Bad material",
            source_reference="test fixture",
            family="test",
            surface_speed_m_min=100.0,
            chip_load_mm_tooth=0.02,
            plunge_ratio=1.1,
            machinability_factor=1.0,
        )


def test_catalog_envelope_rejects_duplicate_item_ids() -> None:
    machine = make_machine()
    with pytest.raises(ValidationError, match="duplicate"):
        CatalogEnvelope[MachineProfile](
            schema_version="1.0",
            catalog_id="machines",
            items=(machine, machine),
        )
