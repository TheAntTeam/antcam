"""3D affine transforms for solid placement.

Mirrors :mod:`antcam_rc2.core.geometry.transform` (Affine2D) but in 3D:
a 4×4 homogeneous matrix stored as numpy array, applied to TriMesh vertices
and Feature3D boundaries. No Qt, no OCP.

Order: scale → rotate (Z*Y*X Euler, degrees) → translate, matching
``TriMesh.rotate`` convention. :func:`compose` applies second first, then first.
"""

from __future__ import annotations

import math

import numpy as np

__all__ = ["Affine3D"]


class Affine3D:
    """An invertible 3D affine transform backed by a 4×4 homogeneous matrix."""

    def __init__(self, matrix: np.ndarray | None = None) -> None:
        if matrix is None:
            self._m = np.eye(4, dtype=np.float64)
        else:
            m = np.asarray(matrix, dtype=np.float64)
            if m.shape != (4, 4):
                raise ValueError("Affine3D matrix must be 4×4")
            self._m = m.copy()

    @property
    def matrix(self) -> np.ndarray:
        """Return a copy of the 4×4 matrix."""
        return self._m.copy()

    @staticmethod
    def identity() -> Affine3D:
        return Affine3D()

    @staticmethod
    def translate(tx: float, ty: float = 0.0, tz: float = 0.0) -> Affine3D:
        m = np.eye(4, dtype=np.float64)
        m[0, 3] = float(tx)
        m[1, 3] = float(ty)
        m[2, 3] = float(tz)
        return Affine3D(m)

    @staticmethod
    def scale(s: float) -> Affine3D:
        """Uniform scale by ``s`` (must be > 0)."""
        if s <= 0:
            raise ValueError("scale must be positive")
        m = np.eye(4, dtype=np.float64) * float(s)
        m[3, 3] = 1.0
        return Affine3D(m)

    @staticmethod
    def rotate(rx_deg: float = 0.0, ry_deg: float = 0.0, rz_deg: float = 0.0) -> Affine3D:
        """Euler rotation Z*Y*X in degrees (matches TriMesh.rotate)."""
        rx, ry, rz = math.radians(rx_deg), math.radians(ry_deg), math.radians(rz_deg)
        cx, sx = math.cos(rx), math.sin(rx)
        cy, sy = math.cos(ry), math.sin(ry)
        cz, sz = math.cos(rz), math.sin(rz)
        Rx = np.array([[1, 0, 0, 0], [0, cx, -sx, 0], [0, sx, cx, 0], [0, 0, 0, 1]], dtype=np.float64)
        Ry = np.array([[cy, 0, sy, 0], [0, 1, 0, 0], [-sy, 0, cy, 0], [0, 0, 0, 1]], dtype=np.float64)
        Rz = np.array([[cz, -sz, 0, 0], [sz, cz, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float64)
        return Affine3D(Rz @ Ry @ Rx)

    @staticmethod
    def from_translation_rotation_scale(
        tx: float = 0.0,
        ty: float = 0.0,
        tz: float = 0.0,
        rx_deg: float = 0.0,
        ry_deg: float = 0.0,
        rz_deg: float = 0.0,
        scale: float = 1.0,
    ) -> Affine3D:
        """Compose scale → rotate → translate (single call helper for placement)."""
        s = Affine3D.scale(scale) if scale != 1.0 else Affine3D.identity()
        r = Affine3D.rotate(rx_deg, ry_deg, rz_deg) if (rx_deg or ry_deg or rz_deg) else Affine3D.identity()
        t = Affine3D.translate(tx, ty, tz) if (tx or ty or tz) else Affine3D.identity()
        # apply s first, then r, then t  =>  t ∘ r ∘ s
        return Affine3D.compose(t, Affine3D.compose(r, s))

    @staticmethod
    def compose(first: Affine3D, second: Affine3D) -> Affine3D:
        """Return ``first ∘ second``: applies ``second`` first, then ``first``."""
        return Affine3D(first._m @ second._m)

    @staticmethod
    def chain(*transforms: Affine3D) -> Affine3D:
        """Compose any number of transforms: ``chain(a,b,c)`` applies c first."""
        result = Affine3D.identity()
        for tr in transforms:
            result = Affine3D.compose(result, tr)
        return result

    def apply_points(self, points: np.ndarray) -> np.ndarray:
        """Apply to ``(N,3)`` points → ``(N,3)``."""
        pts = np.asarray(points, dtype=np.float64)
        if pts.size == 0:
            return pts.copy()
        ones = np.ones((pts.shape[0], 1), dtype=np.float64)
        homo = np.hstack([pts, ones])  # (N,4)
        out = (self._m @ homo.T).T  # (N,4)
        return out[:, :3]

    def apply_vectors(self, vectors: np.ndarray) -> np.ndarray:
        """Apply linear part only to ``(N,3)`` free vectors (no translation)."""
        vecs = np.asarray(vectors, dtype=np.float64)
        if vecs.size == 0:
            return vecs.copy()
        lin = self._m[:3, :3]
        return (lin @ vecs.T).T

    def apply_normal(self, normal: tuple[float, float, float]) -> tuple[float, float, float]:
        """Transform a unit normal via inverse-transpose of linear part, renormalized."""
        lin = self._m[:3, :3]
        # For uniform scale + rotation, inverse-transpose == rotation; but handle generally.
        try:
            inv_t = np.linalg.inv(lin).T
        except np.linalg.LinAlgError:
            return normal
        n = np.asarray(normal, dtype=np.float64)
        tn = inv_t @ n
        length = float(np.linalg.norm(tn))
        if length < 1e-12:
            return normal
        tn /= length
        return (float(tn[0]), float(tn[1]), float(tn[2]))

    def scale_factor(self) -> float:
        """Uniform scale factor (norm of first column of linear part)."""
        col0 = self._m[:3, 0]
        return float(np.linalg.norm(col0))

    def is_identity(self, eps: float = 1e-9) -> bool:
        return bool(np.allclose(self._m, np.eye(4), atol=eps))

    def invert(self) -> Affine3D:
        return Affine3D(np.linalg.inv(self._m))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Affine3D):
            return NotImplemented  # type: ignore[return-value]
        return bool(np.allclose(self._m, other._m, atol=1e-9))

    def __repr__(self) -> str:
        return f"Affine3D({self._m.tolist()!r})"
