"""Tests for neutral, JSON-stable Phase 4 motion contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from antcam_rc2.core.project.models import OperationType
from antcam_rc2.core.toolpath.models import (
    MotionCommand,
    MotionKind,
    MotionProgram,
    OperationPlanStatus,
    Position3,
    ToolpathDiagnostic,
    ToolpathOperationResult,
    ToolpathPlan,
)


def test_motion_models_round_trip_and_reject_invalid_feed_arc_combinations() -> None:
    program = MotionProgram(
        motions=(
            MotionCommand(kind=MotionKind.RAPID, endpoint=Position3(x_mm=0.0, y_mm=0.0, z_mm=10.0)),
            MotionCommand(
                kind=MotionKind.CUT_LINEAR,
                endpoint=Position3(x_mm=10.0, y_mm=0.0, z_mm=0.0),
                feed_mm_min=120.0,
                operation_id="op_1234abcd",
                pass_index=0,
            ),
        )
    )
    restored = MotionProgram.model_validate_json(program.model_dump_json())

    assert restored == program
    with pytest.raises(ValidationError, match="feed_mm_min"):
        MotionCommand(kind=MotionKind.CUT_LINEAR, endpoint=Position3(x_mm=0.0, y_mm=0.0, z_mm=0.0))
    with pytest.raises(ValidationError, match="arc_center"):
        MotionCommand(
            kind=MotionKind.CUT_ARC_CW,
            endpoint=Position3(x_mm=1.0, y_mm=0.0, z_mm=0.0),
            feed_mm_min=50.0,
        )


def test_toolpath_plan_is_executable_only_if_every_enabled_operation_succeeds() -> None:
    succeeded = ToolpathOperationResult(
        operation_id="op_1234abcd",
        operation_type=OperationType.PROFILING,
        status=OperationPlanStatus.SUCCEEDED,
        program=MotionProgram(motions=(MotionCommand(kind=MotionKind.RAPID, endpoint=Position3()),)),
    )
    failed = ToolpathOperationResult(
        operation_id="op_deadbeef",
        operation_type=OperationType.DRILL,
        status=OperationPlanStatus.FAILED,
        diagnostics=(ToolpathDiagnostic(code="geometry_ref_stale", message="stale", severity="error"),),
    )
    plan = ToolpathPlan(project_id="proj_1234abcd", operations=(succeeded, failed))

    assert not plan.is_executable
    assert plan.fingerprint().startswith("sha256:")
    assert plan.model_dump(mode="json")["is_executable"] is False
