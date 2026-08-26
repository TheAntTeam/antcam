"""Tests for core.io.scene."""

from __future__ import annotations

import pytest

from antcam_rc2.core.geometry.curves import LineSegment
from antcam_rc2.core.geometry.primitives import Box2, Point2
from antcam_rc2.core.io.scene import GeometryScene, SceneLayer, SourceInfo
from antcam_rc2.core.units import UnitSystem


def make_scene() -> GeometryScene:
    return GeometryScene(source=SourceInfo(format="dxf"))


class TestSourceInfo:
    def test_defaults(self) -> None:
        info = SourceInfo(format="svg")
        assert info.path == ""
        assert info.version == ""
        assert info.original_units is None

    def test_extra_forbidden(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SourceInfo(format="dxf", nope=1)  # ty: ignore[unknown-argument]


class TestSceneLayer:
    def test_entities_default_empty(self) -> None:
        layer = SceneLayer(name="0")
        assert layer.entities == []


class TestGeometryScene:
    def test_add_entity_creates_layer(self) -> None:
        scene = make_scene()
        scene.add_entity(LineSegment(Point2(0, 0), Point2(10, 0)), "cut")
        layer = scene.layer("cut")
        assert len(layer.entities) == 1

    def test_layer_returns_existing(self) -> None:
        scene = make_scene()
        scene.add_entity(LineSegment(Point2(0, 0), Point2(1, 0)), "a")
        scene.add_entity(LineSegment(Point2(0, 0), Point2(1, 0)), "a")
        assert len(scene.layer("a").entities) == 2

    def test_layer_missing_no_create(self) -> None:
        scene = make_scene()
        with pytest.raises(KeyError):
            scene.layer("nope", create=False)

    def test_iter_entities(self) -> None:
        scene = make_scene()
        scene.add_entity(LineSegment(Point2(0, 0), Point2(1, 0)), "a")
        scene.add_entity(LineSegment(Point2(0, 0), Point2(2, 0)), "b")
        names = [name for name, _ in scene.iter_entities()]
        assert names == ["a", "b"]

    def test_entities_flat(self) -> None:
        scene = make_scene()
        scene.add_entity(LineSegment(Point2(0, 0), Point2(1, 0)), "a")
        assert len(scene.entities()) == 1

    def test_bounding_box_empty(self) -> None:
        assert make_scene().bounding_box() == Box2()

    def test_bounding_box(self) -> None:
        scene = make_scene()
        scene.add_entity(LineSegment(Point2(0, 0), Point2(10, 5)), "a")
        scene.add_entity(LineSegment(Point2(5, -5), Point2(20, 0)), "a")
        assert scene.bounding_box() == Box2(0, -5, 20, 5)

    def test_translate_moves_all_entities(self) -> None:
        scene = make_scene()
        scene.add_entity(LineSegment(Point2(0, 0), Point2(10, 0)), "a")
        scene.add_entity(LineSegment(Point2(0, 5), Point2(10, 5)), "b")
        scene.translate(100.0, 50.0)
        assert scene.bounding_box() == Box2(100, 50, 110, 55)

    def test_normalize_metric_noop(self) -> None:
        scene = make_scene()
        scene.add_entity(LineSegment(Point2(0, 0), Point2(25.4, 0)), "a")
        scene.normalize()
        assert scene.units is UnitSystem.METRIC
        assert scene.bounding_box().width == pytest.approx(25.4)

    def test_normalize_imperial_converts(self) -> None:
        scene = GeometryScene(source=SourceInfo(format="dxf"), units=UnitSystem.IMPERIAL)
        scene.add_entity(LineSegment(Point2(0, 0), Point2(1.0, 0)), "a")
        scene.normalize()
        assert scene.units is UnitSystem.METRIC
        assert scene.bounding_box().width == pytest.approx(25.4)
