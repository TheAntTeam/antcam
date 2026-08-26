"""Tests for the carving strategy family."""

from __future__ import annotations

import pytest

from antcam_rc2.core.errors import OperationError
from antcam_rc2.core.geometry.curves import LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.operations.carving import (
    ChamferingStrategy,
    EngravingStrategy,
    FilletingStrategy,
    VCarveRoughingStrategy,
    VCarvingStrategy,
)
from antcam_rc2.core.toolpath.models import MotionKind


def make_centerline() -> Contour:
    """An open polyline used as a V-groove centerline."""
    return Contour([LineSegment(Point2(0.0, 0.0), Point2(20.0, 0.0))])


def make_closed() -> Contour:
    points = [Point2(0.0, 0.0), Point2(20.0, 0.0), Point2(20.0, 10.0), Point2(0.0, 10.0)]
    return Contour([LineSegment(a, b) for a, b in zip(points, points[1:] + [points[0]], strict=True)])


def test_v_carve_roughing_clears_with_end_mill(make_context) -> None:
    context = make_context(
        op_type="v_carve_roughing",
        tool_id="end_mill_3_175_2f",
        entities=(make_closed(),),
        params={"angle_deg": 60.0, "width_mm": 2.0},
    )
    result = VCarveRoughingStrategy().generate(context)
    assert any(m.kind is MotionKind.CUT_LINEAR for m in result.program.motions)


def test_v_carving_cuts_below_top_clamped_to_groove_depth(make_context) -> None:
    context = make_context(
        op_type="v_carving",
        tool_id="v_bit_30",
        entities=(make_centerline(),),
        params={"angle_deg": 60.0, "width_mm": 2.0},
        depth_mm=2.0,
    )
    result = VCarvingStrategy().generate(context)
    cuts = [m for m in result.program.motions if m.kind is MotionKind.CUT_LINEAR]
    assert cuts
    # V-groove depth for 60deg/2mm is ~1.732mm; the tool must not exceed it.
    assert all(m.endpoint.z_mm >= 5.0 - 1.732 - 1e-3 for m in cuts)


def test_engraving_follows_centerline_at_pass_depth(make_context) -> None:
    context = make_context(
        op_type="engraving", tool_id="v_bit_30", entities=(make_centerline(),), params={}, stepdown_mm=1.0
    )
    result = EngravingStrategy().generate(context)
    assert any(m.kind is MotionKind.CUT_LINEAR for m in result.program.motions)


def test_chamfering_clamps_to_chamfer_depth(make_context) -> None:
    context = make_context(
        op_type="chamfering",
        tool_id="v_bit_30",
        entities=(make_closed(),),
        params={"angle_deg": 45.0, "width_mm": 1.0},
        depth_mm=3.0,
    )
    result = ChamferingStrategy().generate(context)
    cuts = [m for m in result.program.motions if m.kind is MotionKind.CUT_LINEAR]
    assert cuts
    # 45deg chamfer of 1mm width → 1mm depth below top (Z >= 4.0).
    assert all(m.endpoint.z_mm >= 5.0 - 1.0 - 1e-3 for m in cuts)


def test_filleting_rejects_with_3d_capability_code(make_context) -> None:
    context = make_context(
        op_type="filleting",
        tool_id="ball_mill_3_175_2f",
        entities=(make_closed(),),
        params={"radius_mm": 1.0},
    )
    with pytest.raises(OperationError, match="3D"):
        FilletingStrategy().generate(context)


def test_filleting_requires_capability_in_registry() -> None:
    from antcam_rc2.core.operations.registry import build_standard_registry

    definition = build_standard_registry().definition("filleting")
    assert "3d_toolpath" in definition.required_capabilities
