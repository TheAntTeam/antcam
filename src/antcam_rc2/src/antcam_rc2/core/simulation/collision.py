"""Collision detection for the non-cutting tool stack.

Per the Phase 6 design revision (PLAN_FASE_6 6.3.C), responsibilities are:

- ``shank`` (diameter >= cutter, legitimately inside the hole it carved) is
  checked **only against fixtures**;
- ``collet_holder`` / ``spindle`` (wider than the hole) are checked against
  **residual stock material** and fixtures;
- the **work area** constraint applies to the tool tip/path only (never to the
  spindle, which hangs above the bed);
- a deterministic **reach rule** flags ``depth > flute_length + margin``.

All checks are vectorized on sub-arrays with an AABB pre-filter.

Fixture mesh support (PLAN_PRO_FIXTURE):
If a ``Fixture.mesh_path`` is present and points to a valid STEP/STL file,
Phase 6 will load the mesh and perform precise cylinder-vs-mesh collision
detection instead of the AABB box test. The mesh should be loaded via
``import_file_3d`` and tested against the tool bodies using a triangle-AABB
or BVH approach. For now, the box test is used regardless of ``mesh_path``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from antcam_rc2.core.databases.models import MachineProfile
from antcam_rc2.core.project.models import Fixture
from antcam_rc2.core.simulation.models import SimulationCode
from antcam_rc2.core.simulation.tool_geometry import AssemblyBody, ToolAssembly
from antcam_rc2.core.simulation.voxels import VoxelGrid
from antcam_rc2.core.toolpath.models import Position3


@dataclass(frozen=True, slots=True)
class CollisionHit:
    """One collision finding: body, code, position and message."""

    code: SimulationCode
    body_name: str
    position: Position3
    message: str


def check_stock_material(grid: VoxelGrid, body: AssemblyBody, tip: Position3, margin: float) -> CollisionHit | None:
    """Any residual material voxel within the body's radius at its height."""
    z_lo = tip.z_mm + body.z_offset_min - margin
    z_hi = tip.z_mm + body.z_offset_max + margin
    radius = body.radius + margin
    if radius <= 0 or z_hi <= z_lo:
        return None
    grid_z_min = grid.origin[2]
    grid_z_max = grid.origin[2] + grid.shape[2] * grid.voxel_size
    if z_hi <= grid_z_min or z_lo >= grid_z_max:
        return None  # the body is entirely above or below the stock
    z_lo = max(z_lo, grid_z_min)
    z_hi = min(z_hi, grid_z_max)
    i0, j0, k0 = grid.world_to_voxel(tip.x_mm - radius, tip.y_mm - radius, z_lo)
    i1, j1, k1 = grid.world_to_voxel(tip.x_mm + radius, tip.y_mm + radius, z_hi)
    nx = i1 - i0 + 1
    ny = j1 - j0 + 1
    if nx <= 0 or ny <= 0 or k1 < k0:
        return None
    view = grid.slice_view(i0, i1 + 1, j0, j1 + 1, k0, k1 + 1)
    if not np.any(view):
        return None
    xs = grid.origin[0] + (i0 + np.arange(nx, dtype=np.float64) + 0.5) * grid.voxel_size
    ys = grid.origin[1] + (j0 + np.arange(ny, dtype=np.float64) + 0.5) * grid.voxel_size
    distance_sq = (xs[:, None] - tip.x_mm) ** 2 + (ys[None, :] - tip.y_mm) ** 2
    xy_mask = distance_sq <= radius * radius
    hit = np.any(view & xy_mask[:, :, None])
    if hit:
        return CollisionHit(
            code=SimulationCode.COLLISION_STOCK_MATERIAL,
            body_name=body.name,
            position=Position3(x_mm=tip.x_mm, y_mm=tip.y_mm, z_mm=tip.z_mm),
            message=f"{body.name} intersects residual stock material",
        )
    return None


def check_fixture(
    body: AssemblyBody,
    tip: Position3,
    fixtures: tuple[Fixture, ...],
    margin: float,
) -> CollisionHit | None:
    """Cylinder-vs-box test against every fixture (AABB pre-filter then precise)."""
    z_lo = tip.z_mm + body.z_offset_min - margin
    z_hi = tip.z_mm + body.z_offset_max + margin
    radius = body.radius + margin
    for fixture in fixtures:
        box_x0 = fixture.position_x_mm
        box_y0 = fixture.position_y_mm
        box_z0 = fixture.position_z_mm
        box_x1 = box_x0 + fixture.width_mm
        box_y1 = box_y0 + fixture.length_mm
        box_z1 = box_z0 + fixture.height_mm
        if z_hi < box_z0 or z_lo > box_z1:
            continue
        nearest_x = max(box_x0, min(tip.x_mm, box_x1))
        nearest_y = max(box_y0, min(tip.y_mm, box_y1))
        distance = math.hypot(tip.x_mm - nearest_x, tip.y_mm - nearest_y)
        if distance <= radius:
            return CollisionHit(
                code=SimulationCode.COLLISION_FIXTURE,
                body_name=body.name,
                position=Position3(x_mm=tip.x_mm, y_mm=tip.y_mm, z_mm=tip.z_mm),
                message=f"{body.name} intersects fixture '{fixture.name}'",
            )
    return None


def check_tip_in_work_area(tip: Position3, machine: MachineProfile, margin: float) -> CollisionHit | None:
    """The tool tip must stay inside the machine work envelope."""
    inside = (
        -margin <= tip.x_mm <= machine.work_area_x_mm + margin
        and -margin <= tip.y_mm <= machine.work_area_y_mm + margin
        and -margin <= tip.z_mm <= machine.work_area_z_mm + margin
    )
    if inside:
        return None
    return CollisionHit(
        code=SimulationCode.OUTSIDE_WORK_AREA,
        body_name="tip",
        position=Position3(x_mm=tip.x_mm, y_mm=tip.y_mm, z_mm=tip.z_mm),
        message=f"tool tip outside the machine work area ({tip.x_mm:.2f}, {tip.y_mm:.2f}, {tip.z_mm:.2f})",
    )


def check_reach(depth_mm: float, assembly: ToolAssembly, margin: float) -> CollisionHit | None:
    """A deterministic rule: the cut depth must not exceed the flute reach."""
    if depth_mm <= assembly.max_reach_mm() + margin:
        return None
    return CollisionHit(
        code=SimulationCode.REACH_EXCEEDED,
        body_name="cutter",
        position=Position3(),
        message=(
            f"cut depth {depth_mm:.2f} mm exceeds flute reach {assembly.max_reach_mm():.2f} mm + margin {margin:.2f} mm"
        ),
    )


def check_collisions(
    grid: VoxelGrid,
    assembly: ToolAssembly,
    tip: Position3,
    fixtures: tuple[Fixture, ...],
    margin: float,
) -> list[CollisionHit]:
    """Run every applicable check for the current tool position."""
    hits: list[CollisionHit] = []
    for body in assembly.non_cutting_bodies():
        if body.name == "shank":
            hit = check_fixture(body, tip, fixtures, margin)
        else:
            hit = check_stock_material(grid, body, tip, margin) or check_fixture(body, tip, fixtures, margin)
        if hit is not None:
            hits.append(hit)
    return hits


__all__ = [
    "CollisionHit",
    "check_collisions",
    "check_fixture",
    "check_reach",
    "check_stock_material",
    "check_tip_in_work_area",
]
