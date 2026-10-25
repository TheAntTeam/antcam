"""Smooth iso-surface extraction from an occupancy grid (surface nets).

The blocky per-voxel-face mesh produces visible stair steps.  This module
builds a *dual* mesh instead: one vertex per surface cell — the average of the
cell's edge crossings, which rounds the corners — and one quad per
sign-changing lattice edge.  Same voxel resolution, far smoother silhouette.
"""

from __future__ import annotations

import numpy as np

__all__ = ["greedy_surface_mesh", "smooth_surface_mesh"]

# The 12 cell edges as corner codes; a corner is (di << 2) | (dj << 1) | dk.
_EDGES: tuple[tuple[int, int], ...] = (
    (0b000, 0b100),  # along i
    (0b001, 0b101),
    (0b010, 0b110),
    (0b011, 0b111),
    (0b000, 0b010),  # along j
    (0b001, 0b011),
    (0b100, 0b110),
    (0b101, 0b111),
    (0b000, 0b001),  # along k
    (0b010, 0b011),
    (0b100, 0b101),
    (0b110, 0b111),
)


def _greedy_rectangles(mask: np.ndarray):
    """Yield maximal rectangles ``(r0, c0, r1, c1)`` covering the True cells."""
    remaining = mask.copy()
    rows, cols = remaining.shape
    for row in range(rows):
        col = 0
        while col < cols:
            if not remaining[row, col]:
                col += 1
                continue
            col_end = col
            while col_end + 1 < cols and remaining[row, col_end + 1]:
                col_end += 1
            row_end = row
            while row_end + 1 < rows and remaining[row_end + 1, col : col_end + 1].all():
                row_end += 1
            yield row, col, row_end, col_end
            remaining[row : row_end + 1, col : col_end + 1] = False
            col = col_end + 1


def _emit_axis_faces(
    padded: np.ndarray,
    axis: int,
    direction: int,
    origin: np.ndarray,
    size: float,
    vertices: list[np.ndarray],
    triangles: list[np.ndarray],
) -> None:
    """Greedy-merge the boundary faces of one axis/direction into big quads."""
    shp = padded.shape
    lo = [slice(0, n - 1) for n in shp]
    hi = [slice(0, n - 1) for n in shp]
    if direction > 0:
        lo[axis] = slice(0, shp[axis] - 1)
        hi[axis] = slice(1, shp[axis])
    else:
        lo[axis] = slice(1, shp[axis])
        hi[axis] = slice(0, shp[axis] - 1)
    boundary = padded[tuple(lo)] & ~padded[tuple(hi)]

    others = [a for a in range(3) if a != axis]
    first, second = others
    for slice_index in range(boundary.shape[axis]):
        plane = np.take(boundary, slice_index, axis=axis)
        # Keep only the interior (drop the empty padding ring).
        plane = plane[1 : shp[first] - 1, 1 : shp[second] - 1]
        if not plane.any():
            continue
        for r0, c0, r1, c1 in _greedy_rectangles(plane):
            plane_coord = origin[axis] + slice_index * size
            first_low, first_high = origin[first] + r0 * size, origin[first] + (r1 + 1) * size
            second_low, second_high = origin[second] + c0 * size, origin[second] + (c1 + 1) * size
            base = len(vertices)
            # Corner order around the rectangle (varying only on ``first`` and
            # ``second``; the plane coordinate is fixed along ``axis``).
            for use_first_high, use_second_high in ((False, False), (True, False), (True, True), (False, True)):
                corner = [0.0, 0.0, 0.0]
                corner[axis] = plane_coord
                corner[first] = first_high if use_first_high else first_low
                corner[second] = second_high if use_second_high else second_low
                vertices.append(corner)
            if direction > 0:
                triangles.append([base, base + 1, base + 2])
                triangles.append([base, base + 2, base + 3])
            else:
                # Flip the winding so the normal points outward (-axis).
                triangles.append([base, base + 2, base + 1])
                triangles.append([base, base + 3, base + 2])


def greedy_surface_mesh(
    occupied: np.ndarray,
    origin: tuple[float, float, float],
    voxel_size: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Adaptive (greedy) boundary mesh: coplanar faces merged into big quads.

    Flat regions collapse into a handful of large quads and only the actual
    steps keep unit-size faces, which is the practical win of an adaptive
    (octree/quadtree) mesh without the T-junction cracks of a mixed-level
    decomposition.
    """
    occ = np.asarray(occupied, dtype=bool)
    if not occ.any():
        return np.empty((0, 3), dtype=np.float64), np.empty((0, 3), dtype=np.int64)
    nx, ny, nz = occ.shape
    padded = np.zeros((nx + 2, ny + 2, nz + 2), dtype=bool)
    padded[1 : nx + 1, 1 : ny + 1, 1 : nz + 1] = occ
    origin_arr = np.asarray(origin, dtype=np.float64)
    vertices: list[list[float]] = []
    triangles: list[list[int]] = []
    for axis in range(3):
        for direction in (1, -1):
            _emit_axis_faces(padded, axis, direction, origin_arr, float(voxel_size), vertices, triangles)
    if not triangles:
        return np.empty((0, 3), dtype=np.float64), np.empty((0, 3), dtype=np.int64)
    return np.asarray(vertices, dtype=np.float64), np.asarray(triangles, dtype=np.int64)


def _corner_bits(code: int) -> tuple[int, int, int]:
    return (code >> 2) & 1, (code >> 1) & 1, code & 1


def _quads(
    padded: np.ndarray,
    vertex_index: np.ndarray,
    axis: int,
    shape: tuple[int, int, int],
) -> list[np.ndarray]:
    """Emit the two triangles of each quad around a sign-changing lattice edge."""
    # ``a`` = sample at index i, ``b`` = sample at i+1 along ``axis``.
    sl_a = [slice(0, n) for n in padded.shape]
    sl_b = [slice(0, n) for n in padded.shape]
    sl_a[axis] = slice(0, padded.shape[axis] - 1)
    sl_b[axis] = slice(1, padded.shape[axis])
    a = padded[tuple(sl_a)]
    b = padded[tuple(sl_b)]

    # A lattice edge at sample index s is shared by the 4 cells whose lower
    # corner is s-1 or s on the two other axes; keep only the interior samples
    # (cells must exist and their vertex must be defined).
    other = [0, 1, 2]
    other.remove(axis)
    # A lattice edge at sample s is shared by cells s-1 and s, so only samples
    # 1..n (n = the voxel count on that axis) have all 4 cells defined.
    voxels = tuple(n - 2 for n in padded.shape)
    interior = [slice(0, n) for n in padded.shape]
    interior[axis] = slice(0, shape[axis])
    interior[other[0]] = slice(1, 1 + voxels[other[0]])
    interior[other[1]] = slice(1, 1 + voxels[other[1]])
    interior = tuple(interior)
    change = (a != b)[interior]
    if not change.any():
        return []

    # The 4 cells around the edge, in winding order.
    cells = []
    for delta_first, delta_second in ((0, 0), (1, 0), (1, 1), (0, 1)):
        sl = [slice(0, n) for n in vertex_index.shape]
        sl[axis] = slice(0, shape[axis])
        sl[other[0]] = slice(delta_first, delta_first + voxels[other[0]])
        sl[other[1]] = slice(delta_second, delta_second + voxels[other[1]])
        cells.append(vertex_index[tuple(sl)])

    valid = change.copy()
    for cell in cells:
        valid &= cell >= 0
    if not valid.any():
        return []

    v0, v1, v2, v3 = (np.where(valid, cell, 0)[valid] for cell in cells)
    triangles = np.concatenate([np.stack([v0, v1, v2], axis=1), np.stack([v0, v2, v3], axis=1)], axis=0)
    # Flip the winding so normals face outward on one side of the surface.
    flipped = b[interior][valid]
    flip = np.concatenate([flipped, flipped])
    triangles[flip] = triangles[flip][:, ::-1]
    return [triangles]


def smooth_surface_mesh(
    occupied: np.ndarray,
    origin: tuple[float, float, float],
    voxel_size: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Dual (smooth) surface mesh of ``occupied`` in world millimetres."""
    occ = np.asarray(occupied, dtype=bool)
    if not occ.any():
        return np.empty((0, 3), dtype=np.float64), np.empty((0, 3), dtype=np.int64)
    nx, ny, nz = occ.shape
    padded = np.zeros((nx + 2, ny + 2, nz + 2), dtype=bool)
    padded[1 : nx + 1, 1 : ny + 1, 1 : nz + 1] = occ

    cells = (nx + 1, ny + 1, nz + 1)
    corners = {
        code: padded[bits[0] : bits[0] + cells[0], bits[1] : bits[1] + cells[1], bits[2] : bits[2] + cells[2]]
        for code in range(8)
        for bits in [_corner_bits(code)]
    }
    origin_arr = np.asarray(origin, dtype=np.float64)
    size = float(voxel_size)
    axis_index = [
        np.arange(n, dtype=np.float64).reshape([-1 if i == a else 1 for i in range(3)]) for a, n in enumerate(cells)
    ]

    accumulator = np.zeros((*cells, 3), dtype=np.float64)
    crossing_count = np.zeros(cells, dtype=np.int32)
    for first, second in _EDGES:
        a = corners[first]
        b = corners[second]
        crossing = a != b
        if not crossing.any():
            continue
        fa = _corner_bits(first)
        fb = _corner_bits(second)
        for axis in range(3):
            pa = origin_arr[axis] + (axis_index[axis] + fa[axis] - 0.5) * size
            pb = origin_arr[axis] + (axis_index[axis] + fb[axis] - 0.5) * size
            accumulator[..., axis] += np.where(crossing, 0.5 * (pa + pb), 0.0)
        crossing_count += crossing.astype(np.int32)

    surface = crossing_count > 0
    if not surface.any():
        return np.empty((0, 3), dtype=np.float64), np.empty((0, 3), dtype=np.int64)
    vertices = accumulator / np.maximum(crossing_count, 1)[..., None]
    vertex_index = np.full(cells, -1, dtype=np.int64)
    vertex_index[surface] = np.arange(int(surface.sum()))
    points = vertices[surface]

    quads: list[np.ndarray] = []
    for axis in range(3):
        quads.extend(_quads(padded, vertex_index, axis, cells))
    if not quads:
        return points, np.empty((0, 3), dtype=np.int64)
    return points, np.concatenate(quads, axis=0).astype(np.int64)
