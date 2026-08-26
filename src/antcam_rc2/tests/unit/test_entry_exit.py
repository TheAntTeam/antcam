"""Tests for entry/exit motion helpers: plunge, ramp, lead-out, links."""

from __future__ import annotations

from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.toolpath.entry_exit import (
    build_entry_sequence,
    build_exit_sequence,
    build_leadout,
    build_link_motion,
    can_lead_out,
    can_ramp,
)
from antcam_rc2.core.toolpath.models import MotionKind


def test_plunge_entry_is_rapid_then_vertical_cut() -> None:
    motions = build_entry_sequence(Point2(5.0, 5.0), clearance_z=10.0, depth_z=2.0, plunge_feed=50.0)
    assert [m.kind for m in motions] == [MotionKind.RAPID, MotionKind.CUT_LINEAR]
    assert motions[0].endpoint.x_mm == 5.0 and motions[0].endpoint.z_mm == 10.0
    assert motions[1].endpoint.z_mm == 2.0
    assert motions[1].feed_mm_min == 50.0


def test_ramp_entry_approaches_from_minus_x() -> None:
    motions = build_entry_sequence(
        Point2(5.0, 5.0), clearance_z=10.0, depth_z=2.0, plunge_feed=50.0, use_ramp=True, ramp_length_mm=2.0
    )
    assert [m.kind for m in motions] == [MotionKind.RAPID, MotionKind.RAPID, MotionKind.CUT_LINEAR]
    assert motions[1].endpoint.x_mm == 3.0  # 2 mm before start
    assert motions[2].endpoint.x_mm == 5.0 and motions[2].endpoint.z_mm == 2.0


def test_exit_is_rapid_retract_to_clearance() -> None:
    motions = build_exit_sequence(Point2(5.0, 5.0), clearance_z=10.0, depth_z=2.0)
    assert len(motions) == 1
    assert motions[0].kind is MotionKind.RAPID
    assert motions[0].endpoint.z_mm == 10.0


def test_leadout_extends_tangent_to_last_segment() -> None:
    leadout = build_leadout(Point2(10.0, 10.0), Point2(0.0, 10.0), depth_z=2.0, cut_feed=80.0, leadout_length_mm=3.0)
    assert leadout.kind is MotionKind.CUT_LINEAR
    assert leadout.endpoint.x_mm == 13.0  # +X continuation
    assert leadout.endpoint.y_mm == 10.0
    assert leadout.feed_mm_min == 80.0


def test_link_motion_rapids_at_clearance() -> None:
    motions = build_link_motion(Point2(0.0, 0.0), Point2(9.0, 9.0), clearance_z=12.0)
    assert len(motions) == 2
    assert all(m.kind is MotionKind.RAPID for m in motions)
    assert motions[1].endpoint.x_mm == 9.0 and motions[1].endpoint.z_mm == 12.0


def test_can_ramp_requires_approach_room() -> None:
    feasible, length = can_ramp(Point2(10.0, 0.0), Point2(0.0, 0.0), 2.0, 10.0, 3.0)
    assert feasible and length == 1.0
    blocked, _ = can_ramp(Point2(0.5, 0.0), Point2(0.0, 0.0), 2.0, 10.0, 3.0)
    assert not blocked


def test_can_lead_out_needs_tangent_room() -> None:
    feasible, point = can_lead_out(Point2(10.0, 10.0), Point2(0.0, 10.0), 3.0, min_leadout_mm=2.0)
    assert feasible
    assert point is not None and point.x == 12.0
    short, _ = can_lead_out(Point2(10.0, 10.0), Point2(9.9, 10.0), 3.0, min_leadout_mm=2.0)
    assert not short
