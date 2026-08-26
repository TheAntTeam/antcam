"""Tests for the neutral render scene graph contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from antcam_rc2.core.rendering.scene_graph import (
    NodeKind,
    PickEntry,
    RenderBox,
    RenderNode,
    RenderScene,
    flat_points,
)


def test_render_box_union_and_empty() -> None:
    empty = RenderBox.empty()
    assert empty.is_empty
    box = RenderBox(min_x=0.0, min_y=0.0, min_z=0.0, max_x=10.0, max_y=5.0, max_z=2.0)
    assert not box.is_empty
    assert box.width == 10.0 and box.height == 5.0 and box.depth == 2.0
    assert box.center == (5.0, 2.5, 1.0)

    other = RenderBox(min_x=5.0, min_y=0.0, min_z=0.0, max_x=20.0, max_y=5.0, max_z=2.0)
    merged = box.union(other)
    assert merged.to_tuple() == (0.0, 0.0, 0.0, 20.0, 5.0, 2.0)
    assert empty.union(box) == box
    assert box.union(empty) == box
    assert box.expanded(1.0).to_tuple() == (-1.0, -1.0, -1.0, 11.0, 6.0, 3.0)


def test_render_node_validates_shape() -> None:
    strip = RenderNode(kind=NodeKind.LINE_STRIP, points=flat_points([(0.0, 0.0, 0.0), (1.0, 1.0, 0.0)]))
    assert strip.picking_id == 0

    with pytest.raises(ValidationError, match="triplets"):
        RenderNode(kind=NodeKind.LINE_STRIP, points=(0.0, 1.0))
    with pytest.raises(ValidationError, match="requires a box"):
        RenderNode(kind=NodeKind.SOLID_BOX)
    with pytest.raises(ValidationError, match="must not carry"):
        RenderNode(kind=NodeKind.LINE_STRIP, points=(0.0, 0.0, 0.0), box=RenderBox.empty())


def test_scene_bounding_box_and_picking_lookup() -> None:
    scene = RenderScene(
        nodes=(
            RenderNode(kind=NodeKind.LINE_STRIP, points=flat_points([(0.0, 0.0, 0.0), (10.0, 2.0, 0.0)]), picking_id=1),
            RenderNode(
                kind=NodeKind.SOLID_BOX,
                box=RenderBox(min_x=0.0, min_y=0.0, min_z=0.0, max_x=4.0, max_y=4.0, max_z=4.0),
                picking_id=2,
            ),
        ),
        picking=(
            PickEntry(picking_id=1, layer="0", entity_index=0),
            PickEntry(picking_id=2, layer="1", entity_index=0),
        ),
    )

    box = scene.bounding_box()
    assert box.to_tuple() == (0.0, 0.0, 0.0, 10.0, 4.0, 4.0)
    assert scene.pick_entry(1) is not None
    assert scene.pick_entry(1).layer == "0"
    assert scene.pick_entry(99) is None


def test_scene_fingerprint_is_canonical_and_deterministic() -> None:
    node = RenderNode(kind=NodeKind.CLOSED_POLYLINE, points=flat_points([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]))
    first = RenderScene(nodes=(node,)).fingerprint()
    second = RenderScene(nodes=(node,)).fingerprint()
    assert first == second
    assert first.startswith("sha256:")
    other = RenderScene(
        nodes=(RenderNode(kind=NodeKind.CLOSED_POLYLINE, points=flat_points([(0.0, 0.0, 0.0), (2.0, 0.0, 0.0)])),),
    )
    assert first != other.fingerprint()


def test_scene_round_trip_json() -> None:
    scene = RenderScene(
        nodes=(RenderNode(kind=NodeKind.LINE_STRIP, points=flat_points([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])),),
        picking=(PickEntry(picking_id=1, layer="0", entity_index=0),),
    )
    restored = RenderScene.model_validate_json(scene.model_dump_json())
    assert restored == scene
    assert restored.fingerprint() == scene.fingerprint()
