"""Tests for core.io.dxf (import via ezdxf)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from antcam_rc2.core.geometry.curves import Arc, Circle, LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.dxf import import_dxf
from antcam_rc2.core.units import UnitSystem

DATA = Path(__file__).parent.parent / "data"


class TestMiniSquare:
    def test_entities(self) -> None:
        scene = import_dxf(DATA / "mini_square.dxf")
        assert len(scene.entities()) == 4
        assert all(isinstance(e, LineSegment) for e in scene.entities())

    def test_units_metric(self) -> None:
        scene = import_dxf(DATA / "mini_square.dxf")
        assert scene.units is UnitSystem.METRIC
        assert scene.source.format == "dxf"

    def test_bbox(self) -> None:
        scene = import_dxf(DATA / "mini_square.dxf")
        box = scene.bounding_box()
        assert box.width == pytest.approx(10.0)
        assert box.height == pytest.approx(10.0)

    def test_layer(self) -> None:
        scene = import_dxf(DATA / "mini_square.dxf")
        assert len(scene.layers) == 1
        assert scene.layers[0].name == "0"


class TestMiniPolylineBulge:
    def test_contour_closed(self) -> None:
        scene = import_dxf(DATA / "mini_polyline_bulge.dxf")
        contour = scene.entities()[0]
        assert isinstance(contour, Contour)
        assert contour.closed

    def test_contains_arc(self) -> None:
        scene = import_dxf(DATA / "mini_polyline_bulge.dxf")
        contour = scene.entities()[0]
        assert isinstance(contour, Contour)
        arcs = [seg for seg in contour.segments if isinstance(seg, Arc)]
        assert len(arcs) == 1
        # bulge=1.0 -> sweep=pi (semicircle), radius = half the chord (10/2 = 5)
        assert arcs[0].radius == pytest.approx(5.0)
        assert arcs[0].sweep_angle() == pytest.approx(math.pi)

    def test_area(self) -> None:
        scene = import_dxf(DATA / "mini_polyline_bulge.dxf")
        contour = scene.entities()[0]
        assert isinstance(contour, Contour)
        # Square 10x10 + semicircle area (pi*5^2/2) on the top edge
        expected = 100.0 + math.pi * 25.0 / 2.0
        assert contour.area() == pytest.approx(expected, rel=1e-3)


class TestMiniArcCircle:
    def test_entity_types(self) -> None:
        scene = import_dxf(DATA / "mini_arc_circle.dxf")
        types = sorted(type(e).__name__ for e in scene.entities())
        assert types == ["Arc", "Circle", "LineSegment"]

    def test_arc_geometry(self) -> None:
        scene = import_dxf(DATA / "mini_arc_circle.dxf")
        arc = next(e for e in scene.entities() if isinstance(e, Arc))
        assert arc.center == Point2(5.0, 5.0)
        assert arc.radius == pytest.approx(5.0)
        assert arc.sweep_angle() == pytest.approx(math.pi / 2)

    def test_circle_geometry(self) -> None:
        scene = import_dxf(DATA / "mini_arc_circle.dxf")
        circle = next(e for e in scene.entities() if isinstance(e, Circle))
        assert circle.center == Point2(15.0, 15.0)
        assert circle.radius == pytest.approx(2.0)


class TestMiniBlockInsert:
    def test_block_exploded(self) -> None:
        scene = import_dxf(DATA / "mini_block_insert.dxf")
        # The RECT block has 4 lines, all inserted at (1,1)
        assert len(scene.entities()) == 4
        assert all(isinstance(e, LineSegment) for e in scene.entities())

    def test_insert_transform(self) -> None:
        scene = import_dxf(DATA / "mini_block_insert.dxf")
        box = scene.bounding_box()
        # RECT is 4x2 at origin; INSERT moves it to (1,1) -> bbox (1..5, 1..3)
        assert box.min_x == pytest.approx(1.0)
        assert box.min_y == pytest.approx(1.0)
        assert box.max_x == pytest.approx(5.0)
        assert box.max_y == pytest.approx(3.0)


class TestErrors:
    def test_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            import_dxf(DATA / "does_not_exist.dxf")
