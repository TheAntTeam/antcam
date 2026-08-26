"""Tests for core.geometry.paths."""

from __future__ import annotations

import math

import pytest

from antcam_rc2.core.geometry.curves import Arc, LineSegment
from antcam_rc2.core.geometry.paths import Contour, Orientation, Path
from antcam_rc2.core.geometry.primitives import Box2, Point2
from antcam_rc2.core.geometry.transform import Affine2D


def square_contour(side: float = 10.0) -> Contour:
    return Contour(
        [
            LineSegment(Point2(0, 0), Point2(side, 0)),
            LineSegment(Point2(side, 0), Point2(side, side)),
            LineSegment(Point2(side, side), Point2(0, side)),
            LineSegment(Point2(0, side), Point2(0, 0)),
        ]
    )


class TestContour:
    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            Contour([])

    def test_start_end(self) -> None:
        c = square_contour()
        assert c.start == Point2(0, 0)
        assert c.end == Point2(0, 0)

    def test_closed(self) -> None:
        assert square_contour().is_closed()
        open_c = Contour(
            [
                LineSegment(Point2(0, 0), Point2(10, 0)),
                LineSegment(Point2(10, 0), Point2(10, 10)),
            ]
        )
        assert not open_c.is_closed()

    def test_length(self) -> None:
        assert square_contour(10.0).length() == pytest.approx(40.0)

    def test_area_ccw(self) -> None:
        c = square_contour(10.0)
        assert c.area() == pytest.approx(100.0)
        assert c.orientation() == Orientation.CCW

    def test_area_cw(self) -> None:
        c = square_contour(10.0).reverse()
        assert c.area() == pytest.approx(-100.0)
        assert c.orientation() == Orientation.CW

    def test_arc_area(self) -> None:
        # Half disc of radius 2: area = pi * r^2 / 2
        arc = Arc(Point2(0, 0), 2.0, 0.0, math.pi, ccw=True)
        c = Contour(
            [
                arc,
                LineSegment(Point2(-2, 0), Point2(2, 0)),
            ]
        )
        assert c.area() == pytest.approx(math.pi * 2.0)

    def test_reverse(self) -> None:
        c = square_contour()
        r = c.reverse()
        assert r.start == c.end
        assert r.end == c.start
        assert r.area() == pytest.approx(-c.area())

    def test_transform(self) -> None:
        c = square_contour(10.0).transform(Affine2D.translate(5, 5))
        assert c.bounding_box().min_x == pytest.approx(5.0)
        assert c.bounding_box().max_y == pytest.approx(15.0)

    def test_to_polyline_chains(self) -> None:
        c = square_contour(10.0)
        pts = c.to_polyline()
        # 4 segments * 2 points - 4 shared junction points = 4 unique + closing
        assert pts[0] == pts[-1]
        assert len(pts) == 5

    def test_bounding_box(self) -> None:
        c = square_contour(10.0)
        box = c.bounding_box()
        assert box.width == pytest.approx(10.0)
        assert box.height == pytest.approx(10.0)


class TestPath:
    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            Path([])

    def test_length_sum(self) -> None:
        p = Path([square_contour(10.0), square_contour(5.0)])
        assert p.length() == pytest.approx(40.0 + 20.0)

    def test_net_area(self) -> None:
        outer = square_contour(10.0)
        inner = square_contour(4.0).reverse()  # hole
        p = Path([outer, inner])
        assert p.area() == pytest.approx(100.0 - 16.0)

    def test_bounding_box_union(self) -> None:
        c1 = Contour([LineSegment(Point2(0, 0), Point2(1, 0))])
        c2 = Contour([LineSegment(Point2(10, 10), Point2(20, 20))])
        box = Path([c1, c2]).bounding_box()
        assert box == (Box2(0, 0, 20, 20))

    def test_transform(self) -> None:
        p = Path([square_contour(10.0)]).transform(Affine2D.scale(2.0))
        assert p.bounding_box().width == pytest.approx(20.0)

    def test_to_polyline(self) -> None:
        p = Path([square_contour(10.0), square_contour(5.0)])
        polys = p.to_polyline()
        assert len(polys) == 2
        assert len(polys[0]) == 5

    def test_eq(self) -> None:
        assert Path([square_contour(10.0)]) == Path([square_contour(10.0)])
        assert Path([square_contour(10.0)]) != Path([square_contour(5.0)])
