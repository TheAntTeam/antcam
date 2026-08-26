"""Tests for the shared strategy geometry helpers and the contour tracer."""

from __future__ import annotations

import pytest

from antcam_rc2.core.errors import OperationError
from antcam_rc2.core.geometry.curves import Circle, LineSegment
from antcam_rc2.core.geometry.paths import Contour, Path
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.operations.shared import (
    ContourTracer,
    closed_contours,
    drill_center,
    drill_centers,
    entity_polylines,
    inward_distance,
)
from antcam_rc2.core.toolpath.builder import MotionBuilder
from antcam_rc2.core.toolpath.models import MotionKind


def make_closed() -> Contour:
    points = [Point2(0.0, 0.0), Point2(10.0, 0.0), Point2(10.0, 10.0), Point2(0.0, 10.0)]
    return Contour([LineSegment(a, b) for a, b in zip(points, points[1:] + [points[0]], strict=True)])


def test_entity_polylines_for_curve_contour_and_path() -> None:
    line = LineSegment(Point2(0.0, 0.0), Point2(5.0, 0.0))
    assert len(entity_polylines(line, 0.01)) == 1
    assert len(entity_polylines(make_closed(), 0.01)) == 1
    path = Path([make_closed(), make_closed()])
    assert len(entity_polylines(path, 0.01)) == 2


def test_entity_polylines_rejects_unsupported() -> None:
    with pytest.raises(OperationError, match="unsupported"):
        entity_polylines(object(), 0.01)  # ty: ignore[invalid-argument-type]


def test_closed_contours_accepts_closed_and_rejects_open() -> None:
    assert len(closed_contours(make_closed())) == 1
    open_contour = Contour([LineSegment(Point2(0.0, 0.0), Point2(5.0, 0.0))])
    with pytest.raises(OperationError, match="closed"):
        closed_contours(open_contour)
    with pytest.raises(OperationError, match="closed"):
        closed_contours(LineSegment(Point2(0.0, 0.0), Point2(5.0, 0.0)))


def test_inward_distance_depends_on_orientation() -> None:
    ccw = make_closed()
    cw = make_closed().reverse()
    assert inward_distance(ccw, 1.0) == -1.0
    assert inward_distance(cw, 1.0) == 1.0


def test_drill_center_from_circle_curve_and_contour() -> None:
    assert drill_center(Circle(Point2(3.0, 4.0), 1.0)) == Point2(3.0, 4.0)
    assert drill_center(LineSegment(Point2(1.0, 2.0), Point2(3.0, 4.0))) == Point2(1.0, 2.0)
    assert drill_center(make_closed()) == Point2(0.0, 0.0)
    with pytest.raises(OperationError, match="unsupported"):
        drill_center(object())  # ty: ignore[invalid-argument-type]


def test_drill_centers_deduplicates() -> None:
    centers = drill_centers(
        (Circle(Point2(0.0, 0.0), 1.0), Circle(Point2(0.0, 0.0), 2.0), Circle(Point2(5.0, 5.0), 1.0))
    )
    assert len(centers) == 2


def make_tracer() -> tuple[ContourTracer, MotionBuilder]:
    builder = MotionBuilder("op_1234abcd")
    tracer = ContourTracer(
        builder=builder,
        operation_id="op_1234abcd",
        plunge_feed_mm_min=50.0,
        cut_feed_mm_min=100.0,
        clearance_z_mm=10.0,
        tolerance_mm=0.01,
    )
    return tracer, builder


def test_tracer_trace_loops_orders_and_cuts() -> None:
    tracer, builder = make_tracer()
    tracer.trace_loops([make_closed()], depth_z_mm=2.0)
    result = tracer.finish()
    kinds = [m.kind for m in result.program.motions]
    assert kinds[0] is MotionKind.RAPID
    assert MotionKind.CUT_LINEAR in kinds
    assert kinds[-1] is MotionKind.RAPID


def test_tracer_rejects_empty_loops() -> None:
    tracer, _ = make_tracer()
    with pytest.raises(OperationError, match="no usable loops"):
        tracer.trace_loops([], depth_z_mm=2.0)


def test_tracer_attaches_collected_warnings() -> None:
    tracer, _ = make_tracer()
    tracer.add_warning("ramp entry not feasible")
    tracer.trace_loops([make_closed()], depth_z_mm=2.0)
    result = tracer.finish()
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == "entry_degraded"
    assert result.diagnostics[0].severity.value == "warning"
