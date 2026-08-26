"""Tests for core.geometry.primitives."""

from __future__ import annotations

import math

import pytest

from antcam_rc2.core.geometry.primitives import Box2, Point2, Vec2
from antcam_rc2.core.units import TOLERANCE_MM


class TestPoint2:
    def test_distance(self) -> None:
        a = Point2(0.0, 0.0)
        b = Point2(3.0, 4.0)
        assert a.distance_to(b) == pytest.approx(5.0)

    def test_add_sub(self) -> None:
        a = Point2(1.0, 2.0)
        b = Point2(3.0, 4.0)
        assert a + b == Point2(4.0, 6.0)
        assert b - a == Point2(2.0, 2.0)

    def test_eq_within_tolerance(self) -> None:
        assert Point2(0.0, 0.0) == Point2(TOLERANCE_MM / 2, 0.0)
        assert Point2(0.0, 0.0) != Point2(TOLERANCE_MM * 10, 0.0)

    def test_hash_matches_tolerant_eq(self) -> None:
        a = Point2(0.0, 0.0)
        b = Point2(TOLERANCE_MM / 2, 0.0)
        assert hash(a) == hash(b)

    def test_not_point(self) -> None:
        assert Point2(0.0, 0.0) != (0.0, 0.0)


class TestVec2:
    def test_length(self) -> None:
        assert Vec2(3.0, 4.0).length == pytest.approx(5.0)

    def test_normalize(self) -> None:
        v = Vec2(3.0, 4.0).normalize()
        assert v.length == pytest.approx(1.0)
        assert v.x == pytest.approx(0.6)
        assert v.y == pytest.approx(0.8)

    def test_normalize_zero_raises(self) -> None:
        with pytest.raises(ZeroDivisionError):
            Vec2(0.0, 0.0).normalize()

    def test_dot(self) -> None:
        assert Vec2(1.0, 0.0).dot(Vec2(0.0, 1.0)) == pytest.approx(0.0)
        assert Vec2(2.0, 3.0).dot(Vec2(4.0, 5.0)) == pytest.approx(23.0)

    def test_cross(self) -> None:
        assert Vec2(1.0, 0.0).cross(Vec2(0.0, 1.0)) == pytest.approx(1.0)
        assert Vec2(0.0, 1.0).cross(Vec2(1.0, 0.0)) == pytest.approx(-1.0)

    def test_rotate(self) -> None:
        v = Vec2(1.0, 0.0).rotate(math.pi / 2)
        assert v.x == pytest.approx(0.0)
        assert v.y == pytest.approx(1.0)


class TestBox2:
    def test_width_height_center(self) -> None:
        box = Box2(min_x=0.0, min_y=0.0, max_x=10.0, max_y=20.0)
        assert box.width == pytest.approx(10.0)
        assert box.height == pytest.approx(20.0)
        assert box.center == Point2(5.0, 10.0)

    def test_defaults(self) -> None:
        box = Box2()
        assert box.width == pytest.approx(0.0)
        assert box.height == pytest.approx(0.0)

    def test_union(self) -> None:
        a = Box2(0.0, 0.0, 10.0, 10.0)
        b = Box2(5.0, -5.0, 20.0, 5.0)
        u = a.union(b)
        assert u == Box2(0.0, -5.0, 20.0, 10.0)

    def test_contains(self) -> None:
        box = Box2(0.0, 0.0, 10.0, 10.0)
        assert box.contains(Point2(5.0, 5.0))
        assert box.contains(Point2(10.0, 0.0))
        assert not box.contains(Point2(10.5, 0.0))

    def test_expand(self) -> None:
        box = Box2(0.0, 0.0, 10.0, 10.0).expand(2.0)
        assert box == Box2(-2.0, -2.0, 12.0, 12.0)

    def test_eq_tolerant(self) -> None:
        a = Box2(0.0, 0.0, 10.0, 10.0)
        b = Box2(0.0, 0.0, 10.0, 10.0 + TOLERANCE_MM / 2)
        assert a == b
