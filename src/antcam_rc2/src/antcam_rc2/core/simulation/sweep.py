"""Vectorized swept-volume removal for vertical-axis tools.

A vertical-axis cutter moving along a straight segment sweeps a volume whose
XY projection is the distance-to-segment band: voxels within ``radius`` of the
segment in XY and inside the axial Z interval.  Cones (V-bits) and ball-nose
caps use a per-Z-slice radius; arcs and helixes are subdivided into short
segments (chord step <= voxel/2) that reuse the segment logic.
"""

from __future__ import annotations

import math

import numpy as np

from antcam_rc2.core.simulation.tool_geometry import AssemblyBody, BodyShape
from antcam_rc2.core.simulation.voxels import VoxelGrid
from antcam_rc2.core.toolpath.models import MotionCommand, MotionKind, Position3


def remove_segment(
    grid: VoxelGrid,
    p1: Position3,
    p2: Position3,
    radius: float,
    z_min: float,
    z_max: float,
) -> int:
    """Remove voxels within ``radius`` of the XY segment over the Z interval.

    Operates only on the AABB sub-array of the grid (no full-grid passes).
    """
    if radius <= 0:
        return 0
    if z_max < z_min:
        return 0
    size = grid.voxel_size
    min_x = min(p1.x_mm, p2.x_mm) - radius
    max_x = max(p1.x_mm, p2.x_mm) + radius
    min_y = min(p1.y_mm, p2.y_mm) - radius
    max_y = max(p1.y_mm, p2.y_mm) + radius
    i0, j0, k0 = grid.world_to_voxel(min_x, min_y, z_min)
    i1, j1, k1 = grid.world_to_voxel(max_x, max_y, z_max)
    nx = i1 - i0 + 1
    ny = j1 - j0 + 1
    nz = k1 - k0 + 1
    if nx <= 0 or ny <= 0 or nz <= 0:
        return 0

    view = grid.slice_view(i0, i1 + 1, j0, j1 + 1, k0, k1 + 1)
    xs = grid.origin[0] + (i0 + np.arange(nx, dtype=np.float64) + 0.5) * size
    ys = grid.origin[1] + (j0 + np.arange(ny, dtype=np.float64) + 0.5) * size
    zs = grid.origin[2] + (k0 + np.arange(nz, dtype=np.float64) + 0.5) * size

    dx = xs[:, None] - p1.x_mm
    dy = ys[None, :] - p1.y_mm
    vx = p2.x_mm - p1.x_mm
    vy = p2.y_mm - p1.y_mm
    denom = vx * vx + vy * vy
    if denom > 1e-12:
        t = np.clip((dx * vx + dy * vy) / denom, 0.0, 1.0)
        delta_x = dx - t * vx
        delta_y = dy - t * vy
    else:
        delta_x = dx
        delta_y = dy
    distance_sq = delta_x * delta_x + delta_y * delta_y
    xy_mask = distance_sq <= radius * radius
    z_mask = (zs >= z_min) & (zs <= z_max)
    mask = xy_mask[:, :, None] & z_mask[None, None, :]
    return grid.remove_view(view, mask)


def remove_slice(
    grid: VoxelGrid,
    p1: Position3,
    p2: Position3,
    radius: float,
    z_abs: float,
) -> int:
    """Remove voxels in the single Z slice at ``z_abs`` within ``radius`` of the segment."""
    return remove_segment(grid, p1, p2, radius, z_abs, z_abs)


def remove_body_segment(
    grid: VoxelGrid,
    p1: Position3,
    p2: Position3,
    body: AssemblyBody,
    tip_z: float,
) -> int:
    """Remove the swept volume of ``body`` over the sub-segment ``p1 -> p2``.

    The body is treated as stationary in Z at ``tip_z`` (sub-segments are kept
    <= voxel so the error is bounded); cylinders use one vectorized pass, cones
    and ball caps iterate the axial Z slices with the local radius.
    """
    z_lo = tip_z + body.z_offset_min
    z_hi = tip_z + body.z_offset_max
    if body.shape is BodyShape.CYLINDER:
        return remove_segment(grid, p1, p2, body.radius, z_lo, z_hi)
    size = grid.voxel_size
    _, _, k0 = grid.world_to_voxel(p1.x_mm, p1.y_mm, z_lo)
    _, _, k1 = grid.world_to_voxel(p1.x_mm, p1.y_mm, z_hi)
    total = 0
    for k in range(k0, k1 + 1):
        z_abs = grid.origin[2] + (k + 0.5) * size
        radius = body.radius_at(z_abs - tip_z)
        if radius > 0:
            total += remove_slice(grid, p1, p2, radius, z_abs)
    return total


def remove_motion(
    grid: VoxelGrid,
    previous: Position3,
    motion: MotionCommand,
    body: AssemblyBody,
    voxel_size: float,
) -> int:
    """Remove the swept volume of a cutting motion (linear, arc or helix).

    Cylinder bodies have an exact single-pass path for linear motions (the
    swept axial span is ``[min(z1,z2), max(z1,z2)+flute]``); arcs are
    subdivided for curvature and cones/ball caps for their varying radius.
    """
    if motion.kind not in {MotionKind.CUT_LINEAR, MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW}:
        return 0
    if body.shape is BodyShape.CYLINDER:
        if motion.kind is MotionKind.CUT_LINEAR:
            return _remove_cylinder_segment(grid, previous, motion.endpoint, body, body.radius)
        points = _subdivide(previous, motion, voxel_size / 2.0)
        total = 0
        for start, end in zip(points, points[1:], strict=False):
            if start == end:
                continue
            total += _remove_cylinder_segment(grid, start, end, body, body.radius)
        return total

    points = _subdivide(previous, motion, voxel_size)
    total = 0
    for start, end in zip(points, points[1:], strict=False):
        if start == end:
            continue
        tip_z = (start.z_mm + end.z_mm) / 2.0
        total += remove_body_segment(grid, start, end, body, tip_z)
    return total


def _remove_cylinder_segment(
    grid: VoxelGrid,
    start: Position3,
    end: Position3,
    body: AssemblyBody,
    radius: float,
) -> int:
    """Remove a cylinder swept along a segment (exact axial span)."""
    return remove_segment(
        grid,
        start,
        end,
        radius,
        min(start.z_mm, end.z_mm) + body.z_offset_min,
        max(start.z_mm, end.z_mm) + body.z_offset_max,
    )


def _subdivide(previous: Position3, motion: MotionCommand, voxel_size: float) -> list[Position3]:
    """Sample a motion into a polyline with bounded chord step."""
    if motion.kind is MotionKind.CUT_LINEAR:
        length = _distance(previous, motion.endpoint)
        steps = max(1, math.ceil(length / voxel_size))
        return _linear_samples(previous, motion.endpoint, steps)
    if motion.kind in {MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW}:
        if motion.arc_center_xy is None:
            return [previous, motion.endpoint]
        return _arc_samples(previous, motion, voxel_size / 2.0)
    return [previous, motion.endpoint]


def _linear_samples(start: Position3, end: Position3, steps: int) -> list[Position3]:
    return [
        Position3(
            x_mm=start.x_mm + (end.x_mm - start.x_mm) * t,
            y_mm=start.y_mm + (end.y_mm - start.y_mm) * t,
            z_mm=start.z_mm + (end.z_mm - start.z_mm) * t,
        )
        for t in (index / steps for index in range(steps + 1))
    ]


def _arc_samples(start: Position3, motion: MotionCommand, chord_step: float) -> list[Position3]:
    """Flatten an arc/helix into samples with chord step <= ``chord_step``."""
    assert motion.arc_center_xy is not None  # guarded by _subdivide
    center_x, center_y = motion.arc_center_xy
    radius = math.hypot(start.x_mm - center_x, start.y_mm - center_y)
    start_angle = math.atan2(start.y_mm - center_y, start.x_mm - center_x)
    end_angle = math.atan2(motion.endpoint.y_mm - center_y, motion.endpoint.x_mm - center_x)
    if motion.kind is MotionKind.CUT_ARC_CCW:
        sweep = (end_angle - start_angle) % (2.0 * math.pi)
    else:
        sweep = -((start_angle - end_angle) % (2.0 * math.pi))
    if radius <= 1e-9:
        return [start, motion.endpoint]
    if math.hypot(motion.endpoint.x_mm - start.x_mm, motion.endpoint.y_mm - start.y_mm) <= 1e-6 and abs(sweep) <= 1e-6:
        sweep = 2.0 * math.pi if motion.kind is MotionKind.CUT_ARC_CCW else -2.0 * math.pi
    chord_angle = max(1e-3, chord_step / max(radius, 1e-9))
    steps = max(1, math.ceil(abs(sweep) / chord_angle))
    points: list[Position3] = []
    for index in range(steps + 1):
        angle = start_angle + sweep * index / steps
        t = index / steps
        points.append(
            Position3(
                x_mm=center_x + radius * math.cos(angle),
                y_mm=center_y + radius * math.sin(angle),
                z_mm=start.z_mm + (motion.endpoint.z_mm - start.z_mm) * t,
            )
        )
    return points


def _distance(a: Position3, b: Position3) -> float:
    dx = a.x_mm - b.x_mm
    dy = a.y_mm - b.y_mm
    dz = a.z_mm - b.z_mm
    return math.sqrt(dx * dx + dy * dy + dz * dz)


__all__ = ["remove_body_segment", "remove_motion", "remove_segment", "remove_slice"]
