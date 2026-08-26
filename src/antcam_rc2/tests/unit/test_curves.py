"""Tests for core.geometry.curves."""

from __future__ import annotations

import math

import pytest

from antcam_rc2.core.errors import GeometryError
from antcam_rc2.core.geometry.curves import Arc, Circle, LineSegment
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.geometry.transform import Affine2D


class TestLineSegment:
    def test_start_end_length(self) -> None:
        seg = LineSegment(Point2(0, 0), Point2(3, 4))
        assert seg.start == Point2(0, 0)
        assert seg.end == Point2(3, 4)
        assert seg.length() == pytest.approx(5.0)

    def test_point_at(self) -> None:
        seg = LineSegment(Point2(0, 0), Point2(10, 0))
        assert seg.point_at(0.0) == Point2(0, 0)
        assert seg.point_at(0.5) == Point2(5, 0)
        assert seg.point_at(1.0) == Point2(10, 0)

    def test_reverse(self) -> None:
        seg = LineSegment(Point2(0, 0), Point2(10, 0))
        rev = seg.reverse()
        assert rev.start == seg.end
        assert rev.end == seg.start

    def test_transform(self) -> None:
        seg = LineSegment(Point2(0, 0), Point2(10, 0))
        moved = seg.transform(Affine2D.translate(5, 5))
        assert moved.start == Point2(5, 5)
        assert moved.end == Point2(15, 5)

    def test_to_polyline(self) -> None:
        seg = LineSegment(Point2(1, 2), Point2(3, 4))
        assert seg.to_polyline() == [Point2(1, 2), Point2(3, 4)]


class TestCircle:
    def test_radius_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            Circle(Point2(0, 0), -1.0)

    def test_length(self) -> None:
        assert Circle(Point2(0, 0), 1.0).length() == pytest.approx(2 * math.pi)

    def test_start_equals_end(self) -> None:
        c = Circle(Point2(1, 2), 5.0)
        assert c.start == c.end
        assert c.start == Point2(6, 2)

    def test_point_at(self) -> None:
        c = Circle(Point2(0, 0), 1.0)
        assert c.point_at(0.0) == Point2(1, 0)
        assert c.point_at(0.25) == Point2(0, 1)
        assert c.point_at(0.5) == Point2(-1, 0)

    def test_transform_translate(self) -> None:
        c = Circle(Point2(0, 0), 2.0).transform(Affine2D.translate(10, 0))
        assert c.center == Point2(10, 0)
        assert c.radius == pytest.approx(2.0)

    def test_transform_scale(self) -> None:
        c = Circle(Point2(0, 0), 2.0).transform(Affine2D.scale(3.0))
        assert c.radius == pytest.approx(6.0)

    def test_transform_non_uniform_raises(self) -> None:
        with pytest.raises(GeometryError):
            Circle(Point2(0, 0), 2.0).transform(Affine2D.scale(2.0, 3.0))

    def test_to_polyline_closed(self) -> None:
        pts = Circle(Point2(0, 0), 1.0).to_polyline(tolerance=1e-3)
        assert len(pts) > 4
        assert pts[0] == pts[-1]


class TestArc:
    def test_quarter_ccw(self) -> None:
        arc = Arc(Point2(0, 0), 1.0, 0.0, math.pi / 2, ccw=True)
        assert arc.length() == pytest.approx(math.pi / 2)
        assert arc.start == Point2(1, 0)
        assert arc.end == pytest.approx(Point2(0, 1))

    def test_quarter_cw(self) -> None:
        arc = Arc(Point2(0, 0), 1.0, 0.0, -math.pi / 2, ccw=False)
        assert arc.length() == pytest.approx(math.pi / 2)
        assert arc.end == pytest.approx(Point2(0, -1))

    def test_sweep_angle_wraps(self) -> None:
        arc = Arc(Point2(0, 0), 1.0, 3 * math.pi / 2, math.pi / 2, ccw=True)
        assert arc.sweep_angle() == pytest.approx(math.pi)

    def test_reverse(self) -> None:
        arc = Arc(Point2(0, 0), 1.0, 0.0, math.pi / 2, ccw=True)
        rev = arc.reverse()
        assert rev.start == arc.end
        assert rev.end == arc.start
        assert rev.length() == pytest.approx(arc.length())
        assert rev.ccw is False

    def test_transform(self) -> None:
        arc = Arc(Point2(0, 0), 2.0, 0.0, math.pi / 2, ccw=True)
        # rotate(pi/2) applied first, then translate(1,1)
        t = Affine2D.compose(
            Affine2D.translate(1, 1),
            Affine2D.rotate(math.pi / 2),
        )
        moved = arc.transform(t)
        assert moved.center == Point2(1, 1)
        assert moved.radius == pytest.approx(2.0)
        assert moved.start == pytest.approx(Point2(1, 3))
        assert moved.ccw is True

    def test_transform_mirror_flips_ccw(self) -> None:
        arc = Arc(Point2(0, 0), 1.0, 0.0, math.pi / 2, ccw=True)
        moved = arc.transform(Affine2D.mirror_x())
        assert moved.ccw is False
        assert moved.start == pytest.approx(Point2(1, 0))
        assert moved.end == pytest.approx(Point2(0, -1))

    def test_transform_non_uniform_raises(self) -> None:
        with pytest.raises(GeometryError):
            Arc(Point2(0, 0), 1.0, 0.0, math.pi, ccw=True).transform(Affine2D.scale(1.0, 2.0))

    def test_to_polyline(self) -> None:
        arc = Arc(Point2(0, 0), 10.0, 0.0, math.pi, ccw=True)
        pts = arc.to_polyline(tolerance=0.1)
        assert len(pts) > 2
        assert pts[0] == arc.start
        assert pts[-1] == arc.end

    def test_zero_sweep(self) -> None:
        arc = Arc(Point2(0, 0), 1.0, 0.0, 0.0, ccw=True)
        assert arc.length() == pytest.approx(0.0)
        assert arc.point_at(0.5) == Point2(1, 0)
