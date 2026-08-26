"""Voxel grid: monotone material removal on a regular bool lattice.

The grid is a plain ``numpy`` bool array (1 byte per voxel) with an origin and
voxel size in WCS millimetres.  Removal is monotone (``occupied &= ~mask``);
material is never re-added, which matches 2.5D milling semantics and keeps the
hot path allocation-light (views over sub-arrays, no copies).
"""

from __future__ import annotations

import math

import numpy as np

from antcam_rc2.core.project.models import Stock, WorkCoordinateSystem


class VoxelGrid:
    """A boolean occupancy grid aligned with the stock bounding box."""

    __slots__ = ("origin", "voxel_size", "shape", "occupied", "removed_voxels")

    def __init__(
        self,
        *,
        origin: tuple[float, float, float],
        voxel_size: float,
        shape: tuple[int, int, int],
        occupied: np.ndarray,
    ) -> None:
        if voxel_size <= 0:
            raise ValueError("voxel_size must be positive")
        if len(shape) != 3 or any(dimension < 1 for dimension in shape):
            raise ValueError(f"invalid grid shape: {shape}")
        self.origin = origin
        self.voxel_size = float(voxel_size)
        self.shape = shape
        self.occupied = occupied
        self.removed_voxels = 0

    # ------------------------------------------------------------------ creation
    @classmethod
    def from_stock(
        cls,
        stock: Stock,
        wcs: WorkCoordinateSystem,
        resolution_mm: float | None = None,
        max_voxels: int = 32_000_000,
    ) -> tuple[VoxelGrid, float, bool]:
        """Build a fully-occupied grid over the stock box.

        Returns ``(grid, used_resolution, was_clamped)``.  The resolution is
        derived from the stock volume and ``max_voxels`` when not requested,
        and clamped (with ``was_clamped=True``) when a requested resolution
        would exceed the voxel budget.
        """
        width = max(stock.width_mm, 1e-9)
        length = max(stock.length_mm, 1e-9)
        height = max(stock.height_mm, 1e-9)
        volume = width * length * height

        if resolution_mm is None:
            resolution, clamped = _derive_resolution(volume, max_voxels), False
        else:
            count = volume / (resolution_mm**3)
            if count > max_voxels:
                resolution, clamped = _derive_resolution(volume, max_voxels), True
            else:
                resolution, clamped = resolution_mm, False

        shape = (
            max(1, math.ceil(width / resolution)),
            max(1, math.ceil(length / resolution)),
            max(1, math.ceil(height / resolution)),
        )
        origin = (
            stock.position_x_mm + wcs.offset_x_mm,
            stock.position_y_mm + wcs.offset_y_mm,
            stock.position_z_mm + wcs.offset_z_mm,
        )
        grid = cls(
            origin=origin,
            voxel_size=resolution,
            shape=shape,
            occupied=np.ones(shape, dtype=np.bool_),
        )
        return grid, resolution, clamped

    # ------------------------------------------------------------------ mapping
    def world_to_voxel(self, x: float, y: float, z: float) -> tuple[int, int, int]:
        """Grid indices for a world point (clamped into the grid)."""
        nx, ny, nz = self.shape
        i = int(math.floor((x - self.origin[0]) / self.voxel_size))
        j = int(math.floor((y - self.origin[1]) / self.voxel_size))
        k = int(math.floor((z - self.origin[2]) / self.voxel_size))
        return max(0, min(nx - 1, i)), max(0, min(ny - 1, j)), max(0, min(nz - 1, k))

    def voxel_to_world(self, i: int, j: int, k: int) -> tuple[float, float, float]:
        """World coordinates of a voxel centre."""
        size = self.voxel_size
        return (
            self.origin[0] + (i + 0.5) * size,
            self.origin[1] + (j + 0.5) * size,
            self.origin[2] + (k + 0.5) * size,
        )

    # ------------------------------------------------------------------ removal
    def remove_view(self, view: np.ndarray, mask: np.ndarray) -> int:
        """Remove voxels where ``mask`` is True within a ``view`` sub-array.

        ``view`` is a slice view of :attr:`occupied` (no copy); ``mask`` must
        match its shape.  Removal is monotone and the counter is updated.
        """
        removed = int(np.count_nonzero(view & mask))
        view &= ~mask
        self.removed_voxels += removed
        return removed

    def clear_region(self, mask: np.ndarray) -> int:
        """Remove every voxel where ``mask`` is True (full-grid convenience)."""
        return self.remove_view(self.occupied, mask)

    def slice_view(self, x0: int, x1: int, y0: int, y1: int, z0: int, z1: int) -> np.ndarray:
        """A view (no copy) over the ``[x0:x1, y0:y1, z0:z1]`` sub-block."""
        return self.occupied[x0:x1, y0:y1, z0:z1]

    def count_occupied(self) -> int:
        """Number of material voxels still present."""
        return int(np.count_nonzero(self.occupied))

    # ------------------------------------------------------------------ mesh
    def surface_mesh(self, level: float = 0.5):
        """Boundary-quad surface mesh ``(vertices, triangles)``.

        Emits one quad per boundary voxel face that borders empty space
        (axis-aligned, dependency-free).  ``level`` is accepted for interface
        compatibility; the boundary is exact.  Vertices are world coordinates.
        """
        del level
        occupied = self.occupied
        if not np.any(occupied):
            return np.empty((0, 3), dtype=np.float64), np.empty((0, 3), dtype=np.int64)
        size = self.voxel_size
        ox, oy, oz = self.origin
        nx, ny, nz = occupied.shape
        vertices: list[np.ndarray] = []
        triangles: list[np.ndarray] = []
        vertex_offset = 0
        # (axis, direction, corner offsets) for the six faces.
        faces = (
            (0, 1, np.array([(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)], dtype=np.int64)),
            (0, -1, np.array([(0, 0, 1), (0, 1, 1), (0, 1, 0), (0, 0, 0)], dtype=np.int64)),
            (1, 1, np.array([(0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)], dtype=np.int64)),
            (1, -1, np.array([(0, 0, 1), (1, 0, 1), (1, 0, 0), (0, 0, 0)], dtype=np.int64)),
            (2, 1, np.array([(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)], dtype=np.int64)),
            (2, -1, np.array([(0, 1, 0), (1, 1, 0), (1, 0, 0), (0, 0, 0)], dtype=np.int64)),
        )
        for axis, direction, corners in faces:
            indices = _boundary_voxels(occupied, axis, direction)
            if len(indices) == 0:
                continue
            voxel_world = indices * size + np.array([ox, oy, oz])
            quad = voxel_world[:, None, :] + corners[None, :, :] * size
            base = vertex_offset
            vertices.append(quad.reshape(-1, 3))
            count = len(indices)
            triangle_offsets = np.arange(count, dtype=np.int64)[:, None] * 4
            triangles.append(
                np.concatenate(
                    [
                        (triangle_offsets + np.array([0, 1, 2])),
                        (triangle_offsets + np.array([0, 2, 3])),
                    ],
                    axis=0,
                )
                + base
            )
            vertex_offset += count * 4
        if not vertices:
            return np.empty((0, 3), dtype=np.float64), np.empty((0, 3), dtype=np.int64)
        return np.concatenate(vertices, axis=0), np.concatenate(triangles, axis=0)


def _boundary_voxels(occupied: np.ndarray, axis: int, direction: int) -> np.ndarray:
    """Indices of occupied voxels with an empty (or outside) neighbor on a face."""
    neighbor = np.roll(occupied, -direction, axis=axis)
    if direction > 0:
        neighbor[tuple(slice(None) if a != axis else slice(-1, None) for a in range(3))] = False
    else:
        neighbor[tuple(slice(None) if a != axis else slice(0, 1) for a in range(3))] = False
    boundary = occupied & ~neighbor
    return np.argwhere(boundary).astype(np.float64)


def _derive_resolution(volume_mm3: float, max_voxels: int) -> float:
    """Smallest resolution (mm) that fits ``volume`` into ``max_voxels``."""
    size = (volume_mm3 / max_voxels) ** (1.0 / 3.0)
    return max(0.5, size)


__all__ = ["VoxelGrid"]
