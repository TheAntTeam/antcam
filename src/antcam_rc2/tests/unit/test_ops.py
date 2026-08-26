"""Tests for core.geometry.ops."""

from __future__ import annotations

import pytest

from antcam_rc2.core.errors import GeometryError
from antcam_rc2.core.geometry.curves import LineSegment
from antcam_rc2.core.geometry.ops import (
    bounding_box,
    difference,
    intersection,
    offset,
    orient,
    union,
    weld,
)
from antcam_rc2.core.geometry.paths import Contour, Orientation, Path
from antcam_rc2.core.geometry.primitives import Box2, Point2


def square(side: float = 10.0) -> Contour:
    return Contour(
        [
            LineSegment(Point2(0, 0), Point2(side, 0)),
            LineSegment(Point2(side, 0), Point2(side, side)),
            LineSegment(Point2(side, side), Point2(0, side)),
            LineSegment(Point2(0, side), Point2(0, 0)),
        ]
    )


class TestBoundingBox:
    def test_curves(self) -> None:
        box = bounding_box([LineSegment(Point2(0, 0), Point2(10, 5))])
        assert box == Box2(0, 0, 10, 5)

    def test_contour_and_path(self) -> None:
        box = bounding_box([square(10.0), Path([square(5.0)])])
        assert box == Box2(0, 0, 10, 10)

    def test_empty(self) -> None:
        assert bounding_box([]) == Box2()

    def test_unknown_raises(self) -> None:
        with pytest.raises(TypeError):
            bounding_box([object()])  # ty: ignore[invalid-argument-type]


class TestWeld:
    def test_deduplicates_close_points(self) -> None:
        pts = weld([Point2(0, 0), Point2(1e-9, 0), Point2(5, 5)])
        assert pts == [Point2(0, 0), Point2(5, 5)]

    def test_keeps_far_points(self) -> None:
        pts = weld([Point2(0, 0), Point2(1, 0), Point2(2, 0)])
        assert len(pts) == 3


class TestOrient:
    def test_ccw_stays(self) -> None:
        c = square(10.0)  # CCW by default
        assert orient(c).orientation() == Orientation.CCW

    def test_cw_flips_to_ccw(self) -> None:
        c = square(10.0).reverse()
        assert orient(c).orientation() == Orientation.CCW

    def test_orient_to_cw(self) -> None:
        c = square(10.0)
        assert orient(c, Orientation.CW).orientation() == Orientation.CW


class TestOffset:
    def test_expands_square(self) -> None:
        c = square(10.0)
        result = offset(c, 2.0, join_type="miter")
        assert len(result) == 1
        # Offset CCW square outward by 2 → side becomes 10 + 2*2 = 14
        area = abs(result[0].area())
        assert area == pytest.approx(14.0 * 14.0, rel=0.01)

    def test_contracts_square(self) -> None:
        c = square(10.0)
        result = offset(c, -2.0)
        assert len(result) == 1
        area = abs(result[0].area())
        assert area == pytest.approx(6.0 * 6.0, rel=0.01)

    def test_zero_raises(self) -> None:
        with pytest.raises(GeometryError):
            offset(square(10.0), 0.0)

    def test_round_join(self) -> None:
        c = square(10.0)
        result = offset(c, 1.0, join_type="round")
        assert len(result) == 1

    def test_unknown_join_raises(self) -> None:
        with pytest.raises(ValueError):
            offset(square(10.0), 1.0, join_type="bevel")

    def test_path_offset(self) -> None:
        result = offset(Path([square(10.0)]), 1.0)
        assert len(result) == 1


class TestBoolean:
    def test_union_overlapping_squares(self) -> None:
        # Two overlapping squares side by side
        a = Contour(
            [
                LineSegment(Point2(0, 0), Point2(5, 0)),
                LineSegment(Point2(5, 0), Point2(5, 5)),
                LineSegment(Point2(5, 5), Point2(0, 5)),
                LineSegment(Point2(0, 5), Point2(0, 0)),
            ]
        )
        b = Contour(
            [
                LineSegment(Point2(3, 0), Point2(8, 0)),
                LineSegment(Point2(8, 0), Point2(8, 5)),
                LineSegment(Point2(8, 5), Point2(3, 5)),
                LineSegment(Point2(3, 5), Point2(3, 0)),
            ]
        )
        result = union([a, b])
        total = sum(abs(c.area()) for c in result)
        assert total == pytest.approx(40.0, rel=0.01)  # 5x5 + 5x5 - 2x5 overlap

    def test_difference(self) -> None:
        outer = square(10.0)
        inner = Contour(
            [
                LineSegment(Point2(2, 2), Point2(8, 2)),
                LineSegment(Point2(8, 2), Point2(8, 8)),
                LineSegment(Point2(8, 8), Point2(2, 8)),
                LineSegment(Point2(2, 8), Point2(2, 2)),
            ]
        )
        result = difference([outer], [inner])
        # Outer area minus inner area = 100 - 36 = 64 (hole is CW, negative)
        assert abs(sum(c.area() for c in result)) == pytest.approx(64.0, rel=0.01)

    def test_intersection(self) -> None:
        a = Contour(
            [
                LineSegment(Point2(0, 0), Point2(10, 0)),
                LineSegment(Point2(10, 0), Point2(10, 10)),
                LineSegment(Point2(10, 10), Point2(0, 10)),
                LineSegment(Point2(0, 10), Point2(0, 0)),
            ]
        )
        b = Contour(
            [
                LineSegment(Point2(5, 5), Point2(15, 5)),
                LineSegment(Point2(15, 5), Point2(15, 15)),
                LineSegment(Point2(15, 15), Point2(5, 15)),
                LineSegment(Point2(5, 15), Point2(5, 5)),
            ]
        )
        result = intersection([a], [b])
        assert abs(result[0].area()) == pytest.approx(25.0, rel=0.01)

    def test_unknown_op_raises(self) -> None:
        from antcam_rc2.core.geometry.ops import boolean

        with pytest.raises(ValueError):
            boolean([square(10.0)], [], "xor")
