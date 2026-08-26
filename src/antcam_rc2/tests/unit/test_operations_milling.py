"""Tests for the 2.5D milling strategy family."""

from __future__ import annotations

import pytest

from antcam_rc2.core.errors import OperationError
from antcam_rc2.core.geometry.curves import LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.operations.milling import (
    FaceTopStrategy,
    FacingStrategy,
    PocketStrategy,
    ProfileStrategy,
    RoughingStrategy,
    SlottingStrategy,
    TSlottingStrategy,
)
from antcam_rc2.core.toolpath.models import MotionKind


def make_square(size: float = 20.0, x0: float = 5.0) -> Contour:
    points = [Point2(x0, 0.0), Point2(x0 + size, 0.0), Point2(x0 + size, size), Point2(x0, size)]
    return Contour([LineSegment(a, b) for a, b in zip(points, points[1:] + [points[0]], strict=True)])


def test_profile_outside_generates_compensated_cut_motion(make_context) -> None:
    context = make_context(
        op_type="profiling",
        tool_id="end_mill_3_175_2f",
        entities=(make_square(),),
        params={"side": "outside"},
    )
    result = ProfileStrategy().generate(context)
    kinds = [m.kind for m in result.program.motions]
    assert MotionKind.CUT_LINEAR in kinds
    assert result.program.motions[0].kind is MotionKind.RAPID
    assert all(m.endpoint.z_mm <= 5.0 for m in result.program.motions if m.kind is MotionKind.CUT_LINEAR)


def test_profile_ramp_entry_degrades_with_warning(make_context) -> None:
    context = make_context(
        op_type="profiling",
        tool_id="end_mill_3_175_2f",
        entities=(make_square(),),
        params={"side": "outside", "entry_mode": "ramp"},
    )
    result = ProfileStrategy().generate(context)
    assert any(d.code == "entry_degraded" for d in result.diagnostics)


def test_profile_tool_too_large_fails_closed(make_context) -> None:
    context = make_context(
        op_type="profiling",
        tool_id="end_mill_3_175_2f",
        entities=(make_square(size=1.0),),
        params={"side": "inside"},
    )
    with pytest.raises(OperationError):
        ProfileStrategy().generate(context)


def test_face_top_rasterizes_with_allowance(make_context) -> None:
    context = make_context(
        op_type="face_top",
        tool_id="end_mill_3_175_2f",
        entities=(make_square(),),
        params={},
        depth_mm=None,
        stock_allowance_mm=0.5,
    )
    result = FaceTopStrategy().generate(context)
    assert any(m.kind is MotionKind.CUT_LINEAR for m in result.program.motions)
    assert all(m.endpoint.z_mm <= 4.5 + 1e-6 for m in result.program.motions if m.kind is MotionKind.CUT_LINEAR)


def test_face_top_rejects_noop(make_context) -> None:
    context = make_context(
        op_type="face_top",
        tool_id="end_mill_3_175_2f",
        entities=(make_square(),),
        params={},
        depth_mm=None,
        stock_allowance_mm=0.0,
        depth_passes=(4.9,),
    )
    with pytest.raises(OperationError, match="depth_mm"):
        FaceTopStrategy().generate(context)


def test_facing_rasterizes_region(make_context) -> None:
    context = make_context(
        op_type="facing",
        tool_id="end_mill_3_175_2f",
        entities=(make_square(),),
        params={},
        stepdown_mm=1.0,
    )
    result = FacingStrategy().generate(context)
    assert sum(1 for m in result.program.motions if m.kind is MotionKind.CUT_LINEAR) >= 4


def test_pocket_generates_multiple_inward_loops(make_context) -> None:
    context = make_context(
        op_type="pocketing",
        tool_id="end_mill_3_175_2f",
        entities=(make_square(),),
        params={},
        stepdown_mm=1.0,
    )
    result = PocketStrategy().generate(context)
    assert sum(1 for m in result.program.motions if m.kind is MotionKind.RAPID) >= 4


def test_roughing_honors_stock_allowance(make_context) -> None:
    context = make_context(
        op_type="roughing",
        tool_id="end_mill_3_175_2f",
        entities=(make_square(),),
        params={},
        stock_allowance_mm=1.0,
    )
    result = RoughingStrategy().generate(context)
    assert len(result.program.motions) >= 4


def test_slotting_centerline_cuts(make_context) -> None:
    context = make_context(
        op_type="slotting",
        tool_id="end_mill_3_175_2f",
        entities=(make_square(size=20.0),),
        params={"style": "centerline"},
    )
    result = SlottingStrategy().generate(context)
    assert any(m.kind is MotionKind.CUT_LINEAR for m in result.program.motions)


def test_t_slotting_requires_t_slot_tool_via_registry(catalog_bundle) -> None:
    from antcam_rc2.core.operations.registry import build_standard_registry

    definition = build_standard_registry().definition("t_slotting")
    assert definition.allowed_tool_types == frozenset({catalog_bundle.tools["t_slot_6"].tool_type})


def test_t_slotting_generates_motion(make_context) -> None:
    context = make_context(
        op_type="t_slotting",
        tool_id="t_slot_6",
        entities=(make_square(size=20.0),),
        params={},
    )
    result = TSlottingStrategy().generate(context)
    assert any(m.kind is MotionKind.CUT_LINEAR for m in result.program.motions)
