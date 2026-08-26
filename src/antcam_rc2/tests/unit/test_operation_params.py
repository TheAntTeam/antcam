"""Tests for strict per-strategy parameter schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from antcam_rc2.core.operations.params import (
    BoringParameters,
    ChamferingParameters,
    HolePocketingParameters,
    HolesParameters,
    ProfileParameters,
    TappingParameters,
    ThreadMillingParameters,
    VCarvingParameters,
)


def test_profile_parameters_accept_side_and_entry_mode() -> None:
    params = ProfileParameters(side="inside", entry_mode="ramp")
    assert params.side == "inside"
    assert params.entry_mode == "ramp"
    assert ProfileParameters().side == "outside"
    assert ProfileParameters().entry_mode == "plunge"


def test_profile_parameters_reject_unknown_side() -> None:
    with pytest.raises(ValidationError):
        ProfileParameters(side="diagonal")


def test_strategy_parameters_reject_extra_keys() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        ProfileParameters(side="outside", unknown_key=1)


def test_thread_milling_requires_pitch_and_thread_diameter() -> None:
    with pytest.raises(ValidationError, match="pitch_mm"):
        ThreadMillingParameters(thread_diameter_mm=4.0)
    with pytest.raises(ValidationError, match="thread_diameter_mm"):
        ThreadMillingParameters(pitch_mm=0.5)
    params = ThreadMillingParameters(pitch_mm=0.5, thread_diameter_mm=4.0, direction="down")
    assert params.direction == "down"


def test_tapping_requires_positive_pitch() -> None:
    with pytest.raises(ValidationError, match="pitch_mm"):
        TappingParameters()
    with pytest.raises(ValidationError):
        TappingParameters(pitch_mm=0.0)


def test_hole_pocketing_requires_positive_hole_diameter() -> None:
    with pytest.raises(ValidationError, match="hole_diameter_mm"):
        HolePocketingParameters()
    assert HolePocketingParameters(hole_diameter_mm=6.0).hole_diameter_mm == 6.0


def test_boring_dwell_bounds() -> None:
    assert BoringParameters().dwell_seconds == 0.5
    with pytest.raises(ValidationError):
        BoringParameters(dwell_seconds=0.0)
    with pytest.raises(ValidationError):
        BoringParameters(dwell_seconds=100.0)


def test_holes_accepts_both_kinds_with_dwell() -> None:
    assert HolesParameters().hole_type == "drill"
    assert HolesParameters(hole_type="bore", dwell_seconds=1.5).dwell_seconds == 1.5
    with pytest.raises(ValidationError):
        HolesParameters(hole_type="tap")


def test_v_carving_angle_limits() -> None:
    assert VCarvingParameters(angle_deg=60.0, width_mm=2.0).angle_deg == 60.0
    with pytest.raises(ValidationError):
        VCarvingParameters(angle_deg=180.0, width_mm=2.0)
    with pytest.raises(ValidationError):
        VCarvingParameters(angle_deg=0.0, width_mm=2.0)
    with pytest.raises(ValidationError):
        VCarvingParameters(angle_deg=60.0, width_mm=0.0)


def test_chamfering_angle_and_width() -> None:
    assert ChamferingParameters(width_mm=1.0).angle_deg == 45.0
    with pytest.raises(ValidationError):
        ChamferingParameters(width_mm=0.0)


def test_all_models_serialize_to_stable_json_schema() -> None:
    for model in (
        ProfileParameters,
        BoringParameters,
        HolesParameters,
        HolePocketingParameters,
        ThreadMillingParameters,
        TappingParameters,
        VCarvingParameters,
        ChamferingParameters,
    ):
        schema = model.model_json_schema()
        assert schema["additionalProperties"] is False
        assert "properties" in schema
