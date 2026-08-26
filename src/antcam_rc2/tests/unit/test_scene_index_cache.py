"""Scene index cache: hits, explicit invalidation, fingerprint stability, GC."""

from __future__ import annotations

import gc
import weakref

from antcam_rc2.core.geometry.curves import Circle, LineSegment
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.project.geometry_refs import (
    _SCENE_INDEX_CACHE,
    build_scene_index,
    invalidate_scene_index_cache,
    scene_fingerprint,
)


def make_scene() -> GeometryScene:
    scene = GeometryScene(source=SourceInfo(format="dxf", path="cache.dxf"))
    scene.add_entity(LineSegment(Point2(0.0, 0.0), Point2(10.0, 0.0)), "profile")
    scene.add_entity(Circle(Point2(5.0, 5.0), 2.0), "holes")
    return scene


def test_rebuild_returns_same_cached_index() -> None:
    invalidate_scene_index_cache()
    scene = make_scene()
    first = build_scene_index(scene)
    second = build_scene_index(scene)
    assert first is second  # same object from the cache
    assert first.scene_fingerprint == second.scene_fingerprint
    assert _SCENE_INDEX_CACHE.get(scene) is first


def test_invalidate_forces_rebuild() -> None:
    invalidate_scene_index_cache()
    scene = make_scene()
    first = build_scene_index(scene)
    invalidate_scene_index_cache()
    second = build_scene_index(scene)
    assert first is not second
    assert first.scene_fingerprint == second.scene_fingerprint  # deterministic


def test_new_scene_instance_is_not_cached_with_old() -> None:
    invalidate_scene_index_cache()
    a = make_scene()
    b = make_scene()
    index_a = build_scene_index(a)
    assert build_scene_index(b) is not index_a
    assert index_a.scene_fingerprint == build_scene_index(b).scene_fingerprint


def test_cache_dies_with_scene_via_gc() -> None:
    invalidate_scene_index_cache()
    scene = make_scene()
    build_scene_index(scene)
    reference = weakref.ref(scene)
    assert _SCENE_INDEX_CACHE.get(scene) is not None
    del scene
    gc.collect()
    assert reference() is None  # scene collected -> weak key gone


def test_mutated_scene_without_invalidation_is_documented_contract() -> None:
    invalidate_scene_index_cache()
    scene = make_scene()
    before = scene_fingerprint(scene)
    scene.layer("profile", create=False).entities[0] = LineSegment(Point2(0.0, 0.0), Point2(99.0, 0.0))
    # Mutating after indexing is a contract violation; without invalidation the
    # cached fingerprint is returned, with invalidation it reflects the change.
    assert scene_fingerprint(scene) == before
    invalidate_scene_index_cache()
    assert scene_fingerprint(scene) != before
