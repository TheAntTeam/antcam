"""Neutral, UI-agnostic render scene graph shared by every frontend.

The graph is a pure, immutable, JSON-serializable description of what a
frontend must draw: line strips, closed polylines, boxes, toolpath strips and
a ground grid, each with a color, line width, stable picking id and source
provenance.  Both the PySide6 viewport (Phase 5) and the web frontend
(Phase 10) translate this graph into their own GPU/vector pipeline; the core
never depends on a UI toolkit.

Point data is stored as a flat ``(x, y, z, x, y, z, ...)`` float tuple
(stride 3) so graph construction and GPU upload stay allocation-light.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RGBA = tuple[float, float, float, float]

_POINT_STRIDE = 3


class _RenderModel(BaseModel):
    """Shared strict immutable behavior for render graph contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class NodeKind(StrEnum):
    """The primitive kinds a frontend renderer must support."""

    LINE_STRIP = "line_strip"
    CLOSED_POLYLINE = "closed_polyline"
    CIRCLE_OUTLINE = "circle_outline"
    SOLID_BOX = "solid_box"
    BOX_OUTLINE = "box_outline"
    TOOLPATH_STRIP = "toolpath_strip"
    POINT_SET = "point_set"
    GRID = "grid"
    MESH = "mesh"


class RenderBox(_RenderModel):
    """An axis-aligned 3D box used by box nodes, culling and camera fit."""

    min_x: float = 0.0
    min_y: float = 0.0
    min_z: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0
    max_z: float = 0.0

    @classmethod
    def empty(cls) -> RenderBox:
        """An empty box (min > max) that unions with nothing."""
        return cls(min_x=1.0, min_y=1.0, min_z=1.0, max_x=0.0, max_y=0.0, max_z=0.0)

    @property
    def is_empty(self) -> bool:
        return self.min_x > self.max_x or self.min_y > self.max_y or self.min_z > self.max_z

    @property
    def width(self) -> float:
        return max(0.0, self.max_x - self.min_x)

    @property
    def height(self) -> float:
        return max(0.0, self.max_y - self.min_y)

    @property
    def depth(self) -> float:
        return max(0.0, self.max_z - self.min_z)

    @property
    def center(self) -> tuple[float, float, float]:
        return (
            (self.min_x + self.max_x) / 2.0,
            (self.min_y + self.max_y) / 2.0,
            (self.min_z + self.max_z) / 2.0,
        )

    def union(self, other: RenderBox) -> RenderBox:
        """The smallest box containing both boxes."""
        if self.is_empty:
            return other
        if other.is_empty:
            return self
        return RenderBox(
            min_x=min(self.min_x, other.min_x),
            min_y=min(self.min_y, other.min_y),
            min_z=min(self.min_z, other.min_z),
            max_x=max(self.max_x, other.max_x),
            max_y=max(self.max_y, other.max_y),
            max_z=max(self.max_z, other.max_z),
        )

    def expanded(self, margin: float) -> RenderBox:
        """A box grown by ``margin`` on every side."""
        return RenderBox(
            min_x=self.min_x - margin,
            min_y=self.min_y - margin,
            min_z=self.min_z - margin,
            max_x=self.max_x + margin,
            max_y=self.max_y + margin,
            max_z=self.max_z + margin,
        )

    def to_tuple(self) -> tuple[float, float, float, float, float, float]:
        """Return ``(min_x, min_y, min_z, max_x, max_y, max_z)``."""
        return (self.min_x, self.min_y, self.min_z, self.max_x, self.max_y, self.max_z)


class RenderNode(_RenderModel):
    """One drawable primitive with color, width, picking id and provenance.

    Attributes:
        kind: primitive kind.
        points: flat ``(x, y, z, ...)`` float triplets for strip-like kinds.
        box: bounding box for box/grid kinds (mutually exclusive with points).
        color: RGBA in ``[0, 1]``.
        width_px: nominal line width for outline kinds.
        picking_id: stable id returned by GPU picking (``0`` = not pickable).
        layer: source layer name (geometry nodes only).
        operation_id: source operation (toolpath nodes only).
        pass_index: depth pass tag (toolpath nodes only).
        spacing_mm: grid line spacing (grid nodes only).
    """

    kind: NodeKind
    points: tuple[float, ...] = ()
    box: RenderBox | None = None
    color: RGBA = (1.0, 1.0, 1.0, 1.0)
    width_px: float = Field(default=2.0, gt=0)
    picking_id: int = Field(default=0, ge=0)
    layer: str = ""
    operation_id: str | None = None
    pass_index: int | None = Field(default=None, ge=0)
    spacing_mm: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_shape(self) -> RenderNode:
        if self.kind in {NodeKind.SOLID_BOX, NodeKind.BOX_OUTLINE, NodeKind.GRID}:
            if self.box is None:
                raise ValueError(f"{self.kind.value} requires a box")
            if self.points:
                raise ValueError(f"{self.kind.value} must not carry points")
        else:
            if self.box is not None:
                raise ValueError(f"{self.kind.value} must not carry a box")
            if len(self.points) % _POINT_STRIDE != 0:
                raise ValueError("points must be flat x,y,z triplets")
            if self.kind is NodeKind.GRID and self.spacing_mm is None:
                raise ValueError("grid requires spacing_mm")
        return self


class RenderMesh(_RenderModel):
    """A triangle-mesh payload for the 3D viewport (flat, JSON-serializable)."""

    vertices: tuple[float, ...] = ()  # flat (x, y, z, ...) triplets
    triangles: tuple[int, ...] = ()  # flat (i, j, k, ...) triplets
    color: RGBA = (0.7, 0.7, 0.72, 1.0)
    picking_id: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_mesh(self) -> RenderMesh:
        if len(self.vertices) % 3 != 0:
            raise ValueError("vertices must be flat x,y,z triplets")
        if len(self.triangles) % 3 != 0:
            raise ValueError("triangles must be flat i,j,k triplets")
        vertex_count = len(self.vertices) // 3
        if self.triangles and (min(self.triangles) < 0 or max(self.triangles) >= vertex_count):
            raise ValueError("triangles contain out-of-range vertex indices")
        return self


class PickEntry(_RenderModel):
    """Maps a stable picking id back to the source object it represents."""

    picking_id: int = Field(ge=1)
    kind: Literal["geometry", "operation"] = "geometry"
    layer: str | None = None
    entity_index: int | None = Field(default=None, ge=0)
    operation_id: str | None = None


class RenderScene(_RenderModel):
    """An immutable scene graph: ordered nodes, meshes and picking index."""

    nodes: tuple[RenderNode, ...] = ()
    meshes: tuple[RenderMesh, ...] = ()
    picking: tuple[PickEntry, ...] = ()

    def bounding_box(self) -> RenderBox:
        """The overall axis-aligned bounding box of every node and mesh."""
        result = RenderBox.empty()
        for node in self.nodes:
            if node.box is not None:
                result = result.union(node.box)
                continue
            points = node.points
            for index in range(0, len(points), _POINT_STRIDE):
                result = result.union(
                    RenderBox(
                        min_x=points[index],
                        min_y=points[index + 1],
                        min_z=points[index + 2],
                        max_x=points[index],
                        max_y=points[index + 1],
                        max_z=points[index + 2],
                    )
                )
        for mesh in self.meshes:
            for index in range(0, len(mesh.vertices), _POINT_STRIDE):
                result = result.union(
                    RenderBox(
                        min_x=mesh.vertices[index],
                        min_y=mesh.vertices[index + 1],
                        min_z=mesh.vertices[index + 2],
                        max_x=mesh.vertices[index],
                        max_y=mesh.vertices[index + 1],
                        max_z=mesh.vertices[index + 2],
                    )
                )
        return result

    def pick_entry(self, picking_id: int) -> PickEntry | None:
        """Return the source entry for a picking id, if any."""
        for entry in self.picking:
            if entry.picking_id == picking_id:
                return entry
        return None

    def fingerprint(self) -> str:
        """A canonical SHA-256 identity used to avoid redundant graph rebuilds.

        Mesh vertex/triangle arrays are summarized by length (not hashed in
        full) so large 3D scenes do not make fingerprinting O(vertices).
        """
        payload = {
            "nodes": [node.model_dump(mode="json") for node in self.nodes],
            "picking": [entry.model_dump(mode="json") for entry in self.picking],
            "meshes": [
                {
                    "vertices": len(mesh.vertices),
                    "triangles": len(mesh.triangles),
                    "color": mesh.color,
                    "picking_id": mesh.picking_id,
                }
                for mesh in self.meshes
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def flat_points(points: list[tuple[float, float, float]]) -> tuple[float, ...]:
    """Pack ``(x, y, z)`` triplets into a flat tuple for :class:`RenderNode`.

    Uses ``itertools.chain`` (C-implemented) for speed on large graphs.
    """
    return tuple(itertools.chain.from_iterable(points))


__all__ = [
    "NodeKind",
    "PickEntry",
    "RGBA",
    "RenderBox",
    "RenderMesh",
    "RenderNode",
    "RenderScene",
    "flat_points",
]
