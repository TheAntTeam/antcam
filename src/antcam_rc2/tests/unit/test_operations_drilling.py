"""Tests for the drilling strategy family."""

from __future__ import annotations

import pytest

from antcam_rc2.core.errors import OperationError
from antcam_rc2.core.geometry.curves import Circle
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.operations.drilling import (
    BoringStrategy,
    DrillStrategy,
    HolePocketingStrategy,
    HolesStrategy,
    TappingStrategy,
    ThreadMillingStrategy,
)
from antcam_rc2.core.toolpath.models import MotionKind


def make_circle(radius: float = 2.0) -> Circle:
    return Circle(Point2(10.0, 10.0), radius)


def test_drill_emits_rapid_plunge_retract(make_context) -> None:
    context = make_context(op_type="drill", tool_id="drill_2", entities=(make_circle(),), params={})
    result = DrillStrategy().generate(context)
    kinds = [m.kind for m in result.program.motions]
    assert kinds == [MotionKind.RAPID, MotionKind.CUT_LINEAR, MotionKind.RAPID]


def test_drill_multiple_passes_per_center(make_context) -> None:
    context = make_context(op_type="drill", tool_id="drill_2", entities=(make_circle(),), params={}, stepdown_mm=1.0)
    result = DrillStrategy().generate(context)
    assert sum(1 for m in result.program.motions if m.kind is MotionKind.CUT_LINEAR) == 2


def test_boring_adds_dwell(make_context) -> None:
    context = make_context(op_type="boring", tool_id="bore_6", entities=(make_circle(),), params={"dwell_seconds": 1.0})
    result = BoringStrategy().generate(context)
    assert any(m.kind is MotionKind.DWELL and m.dwell_seconds == 1.0 for m in result.program.motions)


def test_holes_dispatches_drill_and_bore(make_context) -> None:
    drill_ctx = make_context(
        op_type="holes", tool_id="drill_2", entities=(make_circle(),), params={"hole_type": "drill"}
    )
    bore_ctx = make_context(op_type="holes", tool_id="bore_6", entities=(make_circle(),), params={"hole_type": "bore"})
    assert any(m.kind is MotionKind.DWELL for m in HolesStrategy().generate(bore_ctx).program.motions)
    assert not any(m.kind is MotionKind.DWELL for m in HolesStrategy().generate(drill_ctx).program.motions)


def test_hole_pocketing_emits_full_circle_arcs(make_context) -> None:
    context = make_context(
        op_type="hole_pocketing",
        tool_id="end_mill_3_175_2f",
        entities=(make_circle(radius=2.0),),
        params={"hole_diameter_mm": 6.0},
        stepdown_mm=1.0,
    )
    result = HolePocketingStrategy().generate(context)
    assert any(m.kind is MotionKind.CUT_ARC_CCW for m in result.program.motions)


def test_hole_pocketing_rejects_hole_smaller_than_tool(make_context) -> None:
    context = make_context(
        op_type="hole_pocketing",
        tool_id="end_mill_3_175_2f",
        entities=(make_circle(),),
        params={"hole_diameter_mm": 2.0},
    )
    with pytest.raises(OperationError, match="hole larger"):
        HolePocketingStrategy().generate(context)


def test_thread_milling_emits_helical_arcs(make_context) -> None:
    context = make_context(
        op_type="thread_milling",
        tool_id="thread_mill_3",
        entities=(make_circle(),),
        params={"pitch_mm": 0.5, "thread_diameter_mm": 4.0},
        depth_mm=3.0,
        stepdown_mm=3.0,
    )
    result = ThreadMillingStrategy().generate(context)
    arcs = [m for m in result.program.motions if m.kind in {MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW}]
    assert len(arcs) >= 16
    z_values = [m.endpoint.z_mm for m in arcs]
    assert max(z_values) > min(z_values)  # the helix travels in Z


def test_thread_milling_rejects_tool_too_large(make_context) -> None:
    context = make_context(
        op_type="thread_milling",
        tool_id="thread_mill_3",
        entities=(make_circle(),),
        params={"pitch_mm": 0.5, "thread_diameter_mm": 3.0},
    )
    with pytest.raises(OperationError, match="too large"):
        ThreadMillingStrategy().generate(context)


def test_tapping_requires_machine_capability_gate(application) -> None:
    from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
    from antcam_rc2.core.project.geometry_refs import create_geometry_ref
    from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
    from antcam_rc2.core.toolpath.settings import PlanningSettings

    scene = GeometryScene(source=SourceInfo(format="dxf"))
    scene.add_entity(make_circle(), "holes")
    project = application.project_service.create_project(
        "tap", machine_id="makera_z1", stock=Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061")
    )
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.TAPPING,
        tool_id="tap_m3",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "holes", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0, strategy_parameters={"pitch_mm": 0.5}),
    )
    plan = application.toolpath_service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=5.0))
    assert plan.operations[0].diagnostics[0].code == "machine_spindle_sync_required"


def test_tapping_strategy_succeeds_on_rigid_machine(make_context, catalog_bundle) -> None:
    rigid = catalog_bundle.machines["makera_z1"].model_copy(update={"rigid_tapping": True})
    context = make_context(
        op_type="tapping",
        tool_id="tap_m3",
        entities=(make_circle(),),
        params={"pitch_mm": 0.5},
        machine_override=rigid,
    )
    result = TappingStrategy().generate(context)
    kinds = [m.kind for m in result.program.motions]
    assert kinds == [MotionKind.RAPID, MotionKind.CUT_LINEAR, MotionKind.CUT_LINEAR]
