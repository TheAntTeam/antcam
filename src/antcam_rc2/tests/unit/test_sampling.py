"""Tests for core.geometry.sampling and transform."""

from __future__ import annotations

import math

import pytest

from antcam_rc2.core.errors import GeometryError
from antcam_rc2.core.geometry.curves import Arc, Circle, LineSegment
from antcam_rc2.core.geometry.primitives import Point2, Vec2
from antcam_rc2.core.geometry.sampling import approximate_to_polyline, sample_curve
from antcam_rc2.core.geometry.transform import Affine2D, apply


class TestAffine2D:
    def test_identity(self) -> None:
        t = Affine2D.identity()
        assert t.apply_point(Point2(3, 4)) == Point2(3, 4)
        assert t.apply_vec(Vec2(3, 4)) == Vec2(3, 4)

    def test_translate(self) -> None:
        t = Affine2D.translate(10, -5)
        assert t.apply_point(Point2(1, 2)) == Point2(11, -3)
        assert t.apply_vec(Vec2(1, 2)) == Vec2(1, 2)

    def test_scale_uniform_and_non(self) -> None:
        assert Affine2D.scale(2.0).apply_point(Point2(3, 4)) == Point2(6, 8)
        assert Affine2D.scale(2.0, 3.0).apply_point(Point2(3, 4)) == Point2(6, 12)

    def test_rotate(self) -> None:
        t = Affine2D.rotate(math.pi / 2)
        p = t.apply_point(Point2(1, 0))
        assert p.x == pytest.approx(0.0)
        assert p.y == pytest.approx(1.0)

    def test_mirror(self) -> None:
        assert Affine2D.mirror_x().apply_point(Point2(1, 2)) == Point2(1, -2)
        assert Affine2D.mirror_y().apply_point(Point2(1, 2)) == Point2(-1, 2)

    def test_determinant_and_flipped(self) -> None:
        assert Affine2D.identity().determinant == pytest.approx(1.0)
        assert not Affine2D.identity().flipped()
        assert Affine2D.mirror_x().flipped()
        assert Affine2D.scale(2.0).determinant == pytest.approx(4.0)

    def test_is_uniform_scale(self) -> None:
        assert Affine2D.scale(3.0).is_uniform_scale()
        assert Affine2D.rotate(0.3).is_uniform_scale()
        assert not Affine2D.scale(2.0, 3.0).is_uniform_scale()
        assert Affine2D.mirror_x().is_uniform_scale()

    def test_scale_factor(self) -> None:
        assert Affine2D.scale(3.0).scale_factor() == pytest.approx(3.0)
        with pytest.raises(GeometryError):
            Affine2D.scale(2.0, 3.0).scale_factor()

    def test_compose_order(self) -> None:
        # translate then rotate must differ from rotate then translate
        a = Affine2D.translate(10, 0)
        b = Affine2D.rotate(math.pi / 2)
        c = Affine2D.compose(a, b)  # b first, then a
        assert c.apply_point(Point2(1, 0)) == pytest.approx(Point2(10, 1))

    def test_eq(self) -> None:
        assert Affine2D.identity() == Affine2D.identity()
        assert Affine2D.translate(1, 1) != Affine2D.translate(1, 2)


class TestApply:
    def test_point_vec(self) -> None:
        assert apply(Affine2D.translate(1, 1), Point2(0, 0)) == Point2(1, 1)
        assert apply(Affine2D.scale(2.0), Vec2(1, 1)) == Vec2(2, 2)

    def test_line(self) -> None:
        seg = LineSegment(Point2(0, 0), Point2(1, 0))
        moved = apply(Affine2D.translate(1, 1), seg)
        assert moved.start == Point2(1, 1)

    def test_circle_non_uniform_raises(self) -> None:
        with pytest.raises(GeometryError):
            apply(Affine2D.scale(1.0, 2.0), Circle(Point2(0, 0), 1.0))

    def test_arc_transform(self) -> None:
        arc = Arc(Point2(0, 0), 1.0, 0.0, math.pi / 2, ccw=True)
        moved = apply(Affine2D.translate(2, 3), arc)
        assert moved.center == Point2(2, 3)

    def test_unsupported_raises(self) -> None:
        with pytest.raises(TypeError):
            apply(Affine2D.identity(), object())  # ty: ignore[no-matching-overload]


class TestSampling:
    def test_line(self) -> None:
        pts = sample_curve(LineSegment(Point2(0, 0), Point2(10, 0)))
        assert pts == [Point2(0, 0), Point2(10, 0)]

    def test_circle_flatten(self) -> None:
        pts = sample_curve(Circle(Point2(0, 0), 5.0), tolerance=0.01)
        assert len(pts) > 16
        assert pts[0] == pts[-1]

    def test_arc_flat_length_matches(self) -> None:
        arc = Arc(Point2(0, 0), 10.0, 0.0, math.pi / 2, ccw=True)
        pts = approximate_to_polyline(arc.point_at, tolerance=0.1)
        assert len(pts) > 2
        assert pts[0] == arc.start
        assert pts[-1] == arc.end

    def test_approximate_respects_tolerance(self) -> None:
        # A very "curvy" evaluator (quarter circle): coarse tolerance yields
        # fewer points than a fine one.
        arc = Arc(Point2(0, 0), 10.0, 0.0, math.pi / 2, ccw=True)
        coarse = approximate_to_polyline(arc.point_at, tolerance=1.0)
        fine = approximate_to_polyline(arc.point_at, tolerance=0.001)
        assert len(coarse) < len(fine)

    def test_adaptive_sample_alias(self) -> None:
        from antcam_rc2.core.geometry.sampling import adaptive_sample

        arc = Arc(Point2(0, 0), 1.0, 0.0, math.pi, ccw=True)
        pts = adaptive_sample(arc.point_at, tolerance=0.05)
        assert pts[0] == arc.start
        assert pts[-1] == arc.end

    def test_max_segments_cap(self) -> None:
        arc = Arc(Point2(0, 0), 1.0, 0.0, math.pi, ccw=True)
        pts = approximate_to_polyline(arc.point_at, tolerance=1e-12, max_segments=8)
        assert len(pts) == 9
