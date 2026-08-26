"""Pure numpy triangle-mesh primitives for the 3D extension.

The 3D pipeline is mesh-first: every solid (STEP or STL) is reduced to a
:class:`TriMesh` and all downstream operations work on numpy arrays only,
with no dependency on OCP or trimesh inside the core models.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, ConfigDict, model_validator

if TYPE_CHECKING:
    from antcam_rc2.core.geometry3d.scene import BoundingBox3D

__all__ = ["TriMesh"]


class TriMesh(BaseModel):
    """An immutable indexed triangle mesh (vertices + faces).

    Attributes:
        vertices: ``(N, 3)`` float64 positions in millimetres.
        faces: ``(M, 3)`` int64 vertex-index triplets.
        face_ids: optional ``(M,)`` int32 group id per triangle (e.g. the
            feature a triangle belongs to); ``None`` means "no grouping".
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    vertices: np.ndarray
    faces: np.ndarray
    face_ids: np.ndarray | None = None

    @model_validator(mode="after")
    def _validate_mesh(self) -> TriMesh:
        vertices = self.vertices
        faces = self.faces
        if vertices.ndim != 2 or vertices.shape[1] != 3:
            raise ValueError("vertices must be an (N, 3) array")
        if faces.ndim != 2 or faces.shape[1] != 3:
            raise ValueError("faces must be an (M, 3) array")
        if faces.dtype.kind not in "iu":
            raise ValueError("faces must contain integer vertex indices")
        if faces.size:
            if int(faces.min()) < 0 or int(faces.max()) >= int(vertices.shape[0]):
                raise ValueError("faces contain out-of-range vertex indices")
        if self.face_ids is not None and int(self.face_ids.shape[0]) != int(faces.shape[0]):
            raise ValueError("face_ids length must match the face count")
        return self

    @property
    def vertex_count(self) -> int:
        """Number of vertices."""
        return int(self.vertices.shape[0])

    @property
    def face_count(self) -> int:
        """Number of triangles."""
        return int(self.faces.shape[0])

    @property
    def is_empty(self) -> bool:
        """True when the mesh has no triangles."""
        return self.face_count == 0

    def face_normals(self) -> np.ndarray:
        """Return unit face normals as an ``(M, 3)`` float64 array."""
        if self.is_empty:
            return np.empty((0, 3), dtype=np.float64)
        triangles = self.vertices[self.faces]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        return np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 1e-12)

    def face_areas(self) -> np.ndarray:
        """Return per-face areas as an ``(M,)`` float64 array."""
        if self.is_empty:
            return np.empty((0,), dtype=np.float64)
        triangles = self.vertices[self.faces]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        return 0.5 * np.linalg.norm(normals, axis=1)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(min_corner, max_corner)`` of the vertex bounding box."""
        if self.vertex_count == 0:
            return np.zeros(3, dtype=np.float64), np.zeros(3, dtype=np.float64)
        return self.vertices.min(axis=0), self.vertices.max(axis=0)

    @property
    def bounding_box(self) -> BoundingBox3D:
        """Return the mesh bounding box as a BoundingBox3D."""
        from antcam_rc2.core.geometry3d.scene import BoundingBox3D

        if self.vertex_count == 0:
            return BoundingBox3D.empty()
        min_corner, max_corner = self.bounds()
        return BoundingBox3D.from_bounds(tuple(min_corner.tolist()), tuple(max_corner.tolist()))

    def as_float64(self) -> TriMesh:
        """Return a copy with canonical float64/int64 dtypes."""
        return TriMesh(
            vertices=np.asarray(self.vertices, dtype=np.float64),
            faces=np.asarray(self.faces, dtype=np.int64),
            face_ids=None if self.face_ids is None else np.asarray(self.face_ids, dtype=np.int32),
        )

    def translate(self, dx: float, dy: float, dz: float) -> TriMesh:
        """Return a new mesh translated by (dx, dy, dz)."""
        if self.is_empty:
            return self
        new_vertices = self.vertices.copy()
        new_vertices[:, 0] += dx
        new_vertices[:, 1] += dy
        new_vertices[:, 2] += dz
        return TriMesh(
            vertices=new_vertices,
            faces=self.faces,
            face_ids=self.face_ids,
        )

    def rotate(self, rx: float, ry: float, rz: float, cx: float = 0.0, cy: float = 0.0, cz: float = 0.0) -> TriMesh:
        """Return a new mesh rotated by (rx, ry, rz) degrees around center (cx, cy, cz)."""
        if self.is_empty:
            return self
        import math

        # Convert degrees to radians
        rx_rad = math.radians(rx)
        ry_rad = math.radians(ry)
        rz_rad = math.radians(rz)
        # Build rotation matrices
        # Rx
        cos_x, sin_x = math.cos(rx_rad), math.sin(rx_rad)
        Rx = np.array(
            [
                [1, 0, 0],
                [0, cos_x, -sin_x],
                [0, sin_x, cos_x],
            ],
            dtype=np.float64,
        )
        # Ry
        cos_y, sin_y = math.cos(ry_rad), math.sin(ry_rad)
        Ry = np.array(
            [
                [cos_y, 0, sin_y],
                [0, 1, 0],
                [-sin_y, 0, cos_y],
            ],
            dtype=np.float64,
        )
        # Rz
        cos_z, sin_z = math.cos(rz_rad), math.sin(rz_rad)
        Rz = np.array(
            [
                [cos_z, -sin_z, 0],
                [sin_z, cos_z, 0],
                [0, 0, 1],
            ],
            dtype=np.float64,
        )
        # Combined rotation matrix (Z * Y * X order)
        R = Rz @ Ry @ Rx
        # Translate to center, rotate, translate back
        new_vertices = self.vertices.copy()
        new_vertices[:, 0] -= cx
        new_vertices[:, 1] -= cy
        new_vertices[:, 2] -= cz
        new_vertices = new_vertices @ R.T
        new_vertices[:, 0] += cx
        new_vertices[:, 1] += cy
        new_vertices[:, 2] += cz
        return TriMesh(
            vertices=new_vertices,
            faces=self.faces,
            face_ids=self.face_ids,
        )
