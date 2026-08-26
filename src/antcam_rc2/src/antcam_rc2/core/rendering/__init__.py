"""Neutral render scene graph shared by the desktop and web frontends.

The graph is UI-agnostic: pure, immutable and JSON-serializable.  The PySide6
viewport (Phase 5) uploads it to the GPU; the web frontend (Phase 10) will
translate the same graph to three.js.
"""

from __future__ import annotations

from antcam_rc2.core.rendering.builder import (
    compose_scenes,
    default_layer_color,
    geometry_to_scene,
    setup_to_scene,
    solid_to_scene,
    toolpath_to_scene,
)
from antcam_rc2.core.rendering.picking import (
    Ray3,
    point_to_segment_distance,
    project_point,
    ray_plane_z,
    unproject,
    view_projection_matrix,
)
from antcam_rc2.core.rendering.scene_graph import (
    RGBA,
    NodeKind,
    PickEntry,
    RenderBox,
    RenderMesh,
    RenderNode,
    RenderScene,
    flat_points,
)

__all__ = [
    "NodeKind",
    "PickEntry",
    "RGBA",
    "Ray3",
    "RenderBox",
    "RenderMesh",
    "RenderNode",
    "RenderScene",
    "compose_scenes",
    "default_layer_color",
    "flat_points",
    "geometry_to_scene",
    "point_to_segment_distance",
    "project_point",
    "ray_plane_z",
    "setup_to_scene",
    "solid_to_scene",
    "toolpath_to_scene",
    "unproject",
    "view_projection_matrix",
]
