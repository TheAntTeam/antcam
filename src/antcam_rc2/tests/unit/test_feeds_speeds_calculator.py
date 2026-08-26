"""Tests for deterministic, explainable feed and speed calculation."""

from __future__ import annotations

import math

import pytest

from antcam_rc2.core.databases.models import (
    CoolingKind,
    CoolingProfile,
    DataStatus,
    MachineProfile,
    MaterialProfile,
    Tool,
    ToolType,
)
from antcam_rc2.core.errors import OperationError
from antcam_rc2.core.feeds_speeds.calculator import FeedsSpeedsCalculator
from antcam_rc2.core.feeds_speeds.models import (
    FeedSpeedOverrides,
    FeedSpeedRequest,
    OperationFamily,
    ValueOrigin,
)


def make_request(**overrides: object) -> FeedSpeedRequest:
    tool = Tool(
        id="end_mill_3_175_2f",
        name="3.175 mm end mill",
        source_reference="test fixture",
        data_status=DataStatus.VERIFIED,
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
        source_reference="test fixture",
        data_status=DataStatus.VERIFIED,
        family="aluminum",
        surface_speed_m_min=250.0,
        chip_load_mm_tooth=0.025,
        plunge_ratio=0.4,
        machinability_factor=1.0,
    )
    machine = MachineProfile(
        id="test_machine",
        name="Test machine",
        source_reference="test fixture",
        data_status=DataStatus.VERIFIED,
        vendor="Test",
        model="M1",
        catalog_version="1.0",
        work_area_x_mm=100.0,
        work_area_y_mm=100.0,
        work_area_z_mm=50.0,
        spindle_power_w=100.0,
        min_rpm=500.0,
        max_rpm=12000.0,
        max_feed_mm_min=500.0,
        collet_sizes_mm=(3.175,),
        default_collet_size_mm=3.175,
        supported_cooling_ids=("air",),
        default_cooling_id="air",
        native_post="test",
        spindle_diameter_mm=50.0,
        spindle_length_mm=100.0,
    )
    cooling = CoolingProfile(
        id="air",
        name="Air",
        source_reference="test fixture",
        data_status=DataStatus.VERIFIED,
        kind=CoolingKind.AIR,
        surface_speed_factor=1.0,
        chip_load_factor=1.0,
    )
    values: dict[str, object] = {
        "tool": tool,
        "material": material,
        "machine": machine,
        "cooling": cooling,
        "operation_family": OperationFamily.MILLING,
    }
    values.update(overrides)
    return FeedSpeedRequest.model_validate(values)


def test_calculator_applies_formula_machine_clamps_and_traceability() -> None:
    result = FeedsSpeedsCalculator().calculate(make_request())

    expected_requested_rpm = (1000.0 * 250.0) / (math.pi * 3.175)
    assert result.requested_rpm == pytest.approx(expected_requested_rpm)
    assert result.rpm == 12000.0
    assert result.cut_feed_mm_min == 500.0
    assert result.plunge_feed_mm_min == 200.0
    assert result.stepdown_mm == pytest.approx(3.175 * 0.5)
    assert result.stepover_mm == pytest.approx(3.175 * 0.4)
    assert result.origins["rpm"] is ValueOrigin.AUTOMATIC
    assert "rpm:max" in result.clamps_applied
    assert "cut_feed_mm_min:max" in result.clamps_applied


def test_manual_values_are_retained_or_clamped_with_explicit_origin() -> None:
    result = FeedsSpeedsCalculator().calculate(
        make_request(
            overrides=FeedSpeedOverrides(
                rpm=8000.0,
                cut_feed_mm_min=750.0,
                plunge_feed_mm_min=150.0,
                stepdown_mm=10.0,
                stepover_mm=0.5,
            )
        )
    )

    assert result.rpm == 8000.0
    assert result.cut_feed_mm_min == 500.0
    assert result.plunge_feed_mm_min == 150.0
    assert result.stepdown_mm == pytest.approx(3.175 * 0.5)
    assert result.stepover_mm == 0.5
    assert result.origins["rpm"] is ValueOrigin.MANUAL
    assert result.origins["cut_feed_mm_min"] is ValueOrigin.MANUAL_CLAMPED
    assert result.origins["plunge_feed_mm_min"] is ValueOrigin.MANUAL
    assert result.origins["stepdown_mm"] is ValueOrigin.MANUAL_CLAMPED
    assert result.origins["stepover_mm"] is ValueOrigin.MANUAL


def test_drilling_has_no_stepover_and_engagement_only_reduces_chip_load() -> None:
    drilling = FeedsSpeedsCalculator().calculate(
        make_request(
            operation_family=OperationFamily.DRILLING,
            radial_engagement_mm=1.0,
            axial_engagement_mm=1.0,
        )
    )
    baseline = FeedsSpeedsCalculator().calculate(make_request(operation_family=OperationFamily.DRILLING))

    assert drilling.stepover_mm is None
    assert drilling.engagement_factor < 1.0
    assert drilling.effective_chip_load_mm_tooth < baseline.effective_chip_load_mm_tooth


def test_tapping_is_rejected_until_pitch_and_spindle_sync_are_modeled() -> None:
    tap = make_request().tool.model_copy(update={"tool_type": ToolType.TAP})

    with pytest.raises(OperationError, match="tapping"):
        FeedsSpeedsCalculator().calculate(make_request(tool=tap, operation_family=OperationFamily.DRILLING))


def test_tapping_requires_machine_rigid_tapping_capability() -> None:
    tap = make_request().tool.model_copy(update={"tool_type": ToolType.TAP})

    with pytest.raises(OperationError, match="rigid tapping") as exc_info:
        FeedsSpeedsCalculator().calculate(
            make_request(tool=tap, operation_family=OperationFamily.DRILLING, pitch_mm=0.5)
        )
    assert exc_info.value.code == "machine_spindle_sync_required"


def test_tapping_requires_pitch_even_on_rigid_machine() -> None:
    tap = make_request().tool.model_copy(update={"tool_type": ToolType.TAP})
    rigid = make_request().machine.model_copy(update={"rigid_tapping": True})

    with pytest.raises(OperationError, match="pitch_mm"):
        FeedsSpeedsCalculator().calculate(
            make_request(tool=tap, machine=rigid, operation_family=OperationFamily.DRILLING)
        )


def test_tapping_feed_equals_rpm_times_pitch_on_rigid_machine() -> None:
    tap = make_request().tool.model_copy(update={"tool_type": ToolType.TAP})
    rigid = make_request().machine.model_copy(update={"rigid_tapping": True, "max_feed_mm_min": 10000.0})

    result = FeedsSpeedsCalculator().calculate(
        make_request(tool=tap, machine=rigid, operation_family=OperationFamily.DRILLING, pitch_mm=0.5)
    )

    assert result.stepover_mm is None
    assert result.plunge_feed_mm_min == pytest.approx(result.cut_feed_mm_min)
    assert result.cut_feed_mm_min == pytest.approx(result.rpm * 0.5)
