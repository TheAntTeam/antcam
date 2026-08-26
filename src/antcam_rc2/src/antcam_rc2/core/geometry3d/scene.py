"""Neutral 3D solid scene: bodies, detected features and fingerprints.

The scene is pure, immutable and JSON-serializable (same philosophy as the 2D
``GeometryScene``): importers produce it, the toolpath service consumes its
features, and the frontend renders its meshes.  No Qt, no OCP, no trimesh here.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from antcam_rc2.core.geometry3d.mesh import TriMesh

__all__ = [
    "BoundingBox3D",
    "Feature3D",
    "FeatureKind",
    "SolidBody",
    "SolidScene",
    "SolidSourceInfo",
]


class BoundingBox3D(BaseModel):
    """Axis-aligned 3D bounding box in millimetres."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_x: float = 0.0
    min_y: float = 0.0
    min_z: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0
    max_z: float = 0.0

    @classmethod
    def empty(cls) -> Self:
        """An empty box (min > max) that unions with nothing."""
        return cls(min_x=1.0, min_y=1.0, min_z=1.0, max_x=0.0, max_y=0.0, max_z=0.0)

    @classmethod
    def from_bounds(cls, min_corner: tuple[float, float, float], max_corner: tuple[float, float, float]) -> Self:
        """Create from (min_x, min_y, min_z), (max_x, max_y, max_z)."""
        return cls(
            min_x=min_corner[0],
            min_y=min_corner[1],
            min_z=min_corner[2],
            max_x=max_corner[0],
            max_y=max_corner[1],
            max_z=max_corner[2],
        )

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

    def union(self, other: Self) -> Self:
        """The smallest box containing both boxes."""
        if self.is_empty:
            return other
        if other.is_empty:
            return self
        return self.__class__(
            min_x=min(self.min_x, other.min_x),
            min_y=min(self.min_y, other.min_y),
            min_z=min(self.min_z, other.min_z),
            max_x=max(self.max_x, other.max_x),
            max_y=max(self.max_y, other.max_y),
            max_z=max(self.max_z, other.max_z),
        )


class FeatureKind(StrEnum):
    """The three 3-axis-machinable feature categories."""

    FACE_PLANAR = "face_planar"
    HOLE = "hole"
    PERIMETER = "perimeter"


class SolidSourceInfo(BaseModel):
    """Provenance of an imported 3D solid file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: str
    path: str = ""


class _SolidModel(BaseModel):
    """Shared strict immutable behavior for the 3D scene models."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Feature3D(_SolidModel):
    """One machinable feature extracted from a solid body.

    ``boundary`` is the closed 3D loop projected onto the feature plane; for a
    ``HOLE`` the loop is a circle described by ``center``/``radius``.  Feature
    coordinates are absolute in the same WCS millimetre space as the mesh.
    """

    kind: FeatureKind
    body_index: int = Field(ge=0)
    feature_index: int = Field(ge=0)
    plane_z_mm: float = 0.0
    plane_normal: tuple[float, float, float] = (0.0, 0.0, 1.0)
    boundary: tuple[tuple[float, float, float], ...] = ()
    triangles: tuple[int, ...] = ()
    center: tuple[float, float] | None = None
    radius: float | None = Field(default=None, ge=0)
    facing: bool = False  # True when the feature is accessible from +Z

    @model_validator(mode="after")
    def _validate_hole(self) -> Feature3D:
        if self.kind is FeatureKind.HOLE:
            if self.center is None or self.radius is None:
                raise ValueError("HOLE features require center and radius")
        return self

    def fingerprint(self, tolerance_mm: float) -> str:
        """A canonical identity for fail-closed persistent references."""
        if tolerance_mm <= 0:
            raise ValueError("tolerance_mm must be positive")

        def quantize(value: float) -> int:
            return round(value / tolerance_mm)

        boundary = [[quantize(v) for v in point] for point in self.boundary]
        payload: dict[str, object] = {
            "kind": self.kind.value,
            "plane_z": quantize(self.plane_z_mm),
            "normal": [quantize(v) for v in self.plane_normal],
            "boundary": boundary,
            "center": None if self.center is None else [quantize(v) for v in self.center],
            "radius": None if self.radius is None else quantize(self.radius),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class SolidBody(_SolidModel):
    """One imported solid: its mesh plus the detected machinable features."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mesh: TriMesh
    features: tuple[Feature3D, ...] = ()

    def triangle_feature_map(self) -> dict[int, int]:
        """Return ``triangle_index -> feature_index`` for O(1) picking lookup."""
        mapping: dict[int, int] = {}
        for feature in self.features:
            for triangle in feature.triangles:
                mapping[int(triangle)] = feature.feature_index
        return mapping

    def translate(self, dx: float, dy: float, dz: float) -> SolidBody:
        """Return a new body with the mesh translated by (dx, dy, dz)."""
        return SolidBody(
            id=self.id,
            name=self.name,
            mesh=self.mesh.translate(dx, dy, dz),
            features=self.features,
        )

    def rotate(self, rx: float, ry: float, rz: float, cx: float = 0.0, cy: float = 0.0, cz: float = 0.0) -> SolidBody:
        """Return a new body with the mesh rotated by (rx, ry, rz) degrees around center (cx, cy, cz)."""
        return SolidBody(
            id=self.id,
            name=self.name,
            mesh=self.mesh.rotate(rx, ry, rz, cx, cy, cz),
            features=self.features,
        )


class SolidScene(_SolidModel):
    """An immutable imported 3D scene: ordered bodies and their features."""

    source: SolidSourceInfo
    bodies: tuple[SolidBody, ...] = ()
    units: str = "metric"
    tolerance_mm: float = Field(default=1e-6, gt=0)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def iter_features(self):
        """Yield ``(body_index, feature)`` pairs in deterministic order."""
        for body_index, body in enumerate(self.bodies):
            for feature in body.features:
                yield body_index, feature

    def feature_count(self) -> int:
        """Total number of detected features."""
        return sum(len(body.features) for body in self.bodies)

    def has_errors(self) -> bool:
        """True when the import recorded blocking errors."""
        return bool(self.errors)

    def fingerprint(self) -> str:
        """A canonical identity for the whole scene (bodies + features)."""
        payload: list[dict[str, object]] = []
        for body in self.bodies:
            body_payload: dict[str, object] = {
                "id": body.id,
                "name": body.name,
                "vertices": int(body.mesh.vertex_count),
                "faces": int(body.mesh.face_count),
                "features": [feature.fingerprint(self.tolerance_mm) for feature in body.features],
            }
            payload.append(body_payload)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def bounding_box(self) -> BoundingBox3D:
        """Return the overall bounding box of all meshes in the scene."""
        if not self.bodies:
            return BoundingBox3D.empty()
        box = BoundingBox3D.empty()
        for body in self.bodies:
            box = box.union(body.mesh.bounding_box)
        return box

    def translate(self, dx: float, dy: float, dz: float) -> SolidScene:
        """Return a new scene with all bodies translated by (dx, dy, dz)."""
        translated_bodies = tuple(body.translate(dx, dy, dz) for body in self.bodies)
        return SolidScene(
            source=self.source,
            bodies=translated_bodies,
            units=self.units,
            tolerance_mm=self.tolerance_mm,
            warnings=self.warnings,
            errors=self.errors,
        )

    def rotate(self, rx: float, ry: float, rz: float, cx: float = 0.0, cy: float = 0.0, cz: float = 0.0) -> SolidScene:
        """Return a new scene with all bodies rotated by (rx, ry, rz) degrees around center (cx, cy, cz)."""
        rotated_bodies = tuple(body.rotate(rx, ry, rz, cx, cy, cz) for body in self.bodies)
        return SolidScene(
            source=self.source,
            bodies=rotated_bodies,
            units=self.units,
            tolerance_mm=self.tolerance_mm,
            warnings=self.warnings,
            errors=self.errors,
        )
