"""Tests for the simulation domain contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from antcam_rc2.core.simulation.models import (
    SimulationCode,
    SimulationEvent,
    SimulationReport,
    SimulationSettings,
    SimulationSeverity,
    SimulationStats,
    SimulationTick,
)
from antcam_rc2.core.toolpath.models import Position3


def test_settings_validation() -> None:
    assert SimulationSettings().timeline_max_ticks == 4096
    with pytest.raises(ValidationError):
        SimulationSettings(voxel_resolution_mm=0.0)
    with pytest.raises(ValidationError):
        SimulationSettings(checkpoint_every=0)
    with pytest.raises(ValidationError):
        SimulationSettings(max_voxels=10)


def test_event_and_tick_round_trip() -> None:
    event = SimulationEvent(
        tick=3,
        motion_index=7,
        severity=SimulationSeverity.CRITICAL,
        code=SimulationCode.COLLISION_FIXTURE,
        message="spindle intersects fixture",
        position=Position3(x_mm=1.0, y_mm=2.0, z_mm=3.0),
        operation_id="op_1234abcd",
    )
    restored = SimulationEvent.model_validate_json(event.model_dump_json())
    assert restored == event

    tick = SimulationTick(
        index=3,
        motion_index=7,
        tool_position=Position3(x_mm=1.0, y_mm=2.0, z_mm=3.0),
        removed_voxels=10,
        total_removed_mm3=10.0,
        collision_count=1,
        event_indices=(0,),
    )
    assert SimulationTick.model_validate_json(tick.model_dump_json()) == tick


def test_report_fingerprint_deterministic_and_round_trip() -> None:
    report = SimulationReport(
        project_id="proj_1234abcd",
        plan_fingerprint="sha256:abc",
        settings=SimulationSettings(voxel_resolution_mm=1.0),
        events=(
            SimulationEvent(
                tick=0,
                motion_index=0,
                severity=SimulationSeverity.INFO,
                code=SimulationCode.OPERATION_SKIPPED,
                message="skipped",
                position=Position3(),
            ),
        ),
        stats=SimulationStats(initial_voxels=100, removed_voxels=5, removed_mm3=5.0),
    )
    assert report.fingerprint() == report.fingerprint()
    assert report.fingerprint().startswith("sha256:")
    restored = SimulationReport.model_validate_json(report.model_dump_json())
    assert restored == report
    assert restored.fingerprint() == report.fingerprint()
