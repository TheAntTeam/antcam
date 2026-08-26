"""Tests for safe persistent references to transient imported geometry."""

from __future__ import annotations

import pytest

from antcam_rc2.core.errors import GeometryReferenceError
from antcam_rc2.core.geometry.curves import LineSegment
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.project.geometry_refs import (
    bind_geometry,
    create_geometry_ref,
    entity_fingerprint,
    resolve_geometry_ref,
)


def make_scene() -> GeometryScene:
    scene = GeometryScene(source=SourceInfo(format="dxf", path="drawing.dxf"))
    scene.add_entity(LineSegment(Point2(0.0, 0.0), Point2(10.0, 0.0)), "cut")
    scene.add_entity(LineSegment(Point2(0.0, 2.0), Point2(10.0, 2.0)), "cut")
    return scene


def test_geometry_ref_resolves_an_exact_index_and_fingerprint_match() -> None:
    scene = make_scene()
    reference = create_geometry_ref(scene, "cut", 1)

    assert resolve_geometry_ref(reference, scene) == scene.layer("cut", create=False).entities[1]
    assert reference.entity_fingerprint == entity_fingerprint(
        scene.layer("cut", create=False).entities[1], scene.tolerance_mm
    )
    assert bind_geometry(scene).source.path == "drawing.dxf"


def test_geometry_ref_falls_back_to_an_exact_fingerprint_after_reordering() -> None:
    scene = make_scene()
    reference = create_geometry_ref(scene, "cut", 1)
    layer = scene.layer("cut", create=False)
    layer.entities.reverse()

    resolved = resolve_geometry_ref(reference, scene)

    assert resolved == layer.entities[0]


def test_geometry_ref_rejects_changed_or_missing_geometry_instead_of_retargeting() -> None:
    scene = make_scene()
    reference = create_geometry_ref(scene, "cut", 1)
    layer = scene.layer("cut", create=False)
    layer.entities[1] = LineSegment(Point2(0.0, 5.0), Point2(10.0, 5.0))

    with pytest.raises(GeometryReferenceError, match="stale"):
        resolve_geometry_ref(reference, scene)

    layer.entities.append(LineSegment(Point2(0.0, 2.0), Point2(10.0, 2.0)))
    layer.entities.append(LineSegment(Point2(0.0, 2.0), Point2(10.0, 2.0)))
    with pytest.raises(GeometryReferenceError, match="ambiguous"):
        resolve_geometry_ref(reference, scene)
