"""Headless simulation orchestration: ToolpathPlan -> SimulationReport.

The simulator is pure and deterministic: it reads an immutable ``Project`` and
``ToolpathPlan`` snapshot, resolves tools from the catalog, removes material on
a voxel grid, checks collisions per the Phase 6 body-responsibility model and
produces a :class:`SimulationReport` plus transient checkpoints for UI
scrubbing.  It never mutates project, plan or catalog state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from antcam_rc2.core.databases.repository import CatalogRepository
from antcam_rc2.core.project.models import Fixture, Project
from antcam_rc2.core.project.stock_geometry import stock_min_corner, stock_top_z
from antcam_rc2.core.simulation.collision import (
    check_collisions,
    check_reach,
    check_tip_in_work_area,
)
from antcam_rc2.core.simulation.models import (
    SimulationCode,
    SimulationEvent,
    SimulationReport,
    SimulationSettings,
    SimulationSeverity,
    SimulationStats,
    SimulationTick,
)
from antcam_rc2.core.simulation.sweep import _subdivide, remove_motion
from antcam_rc2.core.simulation.tool_geometry import ToolAssembly
from antcam_rc2.core.simulation.voxels import VoxelGrid
from antcam_rc2.core.toolpath.models import (
    MotionCommand,
    MotionKind,
    OperationPlanStatus,
    Position3,
    ToolpathPlan,
)


@dataclass(frozen=True, slots=True)
class VoxelCheckpoint:
    """A copy of the occupancy mask at one tick (for UI scrubbing)."""

    tick_index: int
    motion_index: int
    mask: np.ndarray


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """The report plus transient per-tick mask checkpoints."""

    report: SimulationReport
    checkpoints: tuple[VoxelCheckpoint, ...] = ()


class Simulator:
    """Deterministic voxel simulation of one toolpath plan."""

    def __init__(self, catalog_repository: CatalogRepository) -> None:
        self._catalogs = catalog_repository

    def simulate(
        self,
        project: Project,
        plan: ToolpathPlan,
        settings: SimulationSettings,
        *,
        progress_callback=None,
        progress_every: int = 8,
        mesh_every: int = 32,
    ) -> SimulationResult:
        """Simulate the whole plan; returns report + scrubbing checkpoints.

        ``progress_callback(motion_index, tool_position, removed_voxels,
        occupancy_or_None)`` is invoked in the caller thread every
        ``progress_every`` motions (occupancy is a full mask copy every
        ``mesh_every`` motions, for the UI to build display meshes).  It is
        optional and does not affect the report.
        """
        events: list[SimulationEvent] = []
        accumulator = _StatsAccumulator()
        if not plan.is_executable:
            events.append(
                self._event(
                    tick=0,
                    motion_index=0,
                    severity=SimulationSeverity.ERROR,
                    code=SimulationCode.PLAN_NOT_EXECUTABLE,
                    message="toolpath plan is not executable; simulation skipped",
                    position=Position3(),
                )
            )
            report = SimulationReport(
                project_id=project.id,
                plan_fingerprint=plan.fingerprint(),
                settings=settings,
                events=tuple(events),
                stats=accumulator.build(grid=None),
            )
            return SimulationResult(report=report)

        grid, used_resolution, was_clamped = VoxelGrid.from_stock(
            project.stock,
            project.wcs,
            settings.voxel_resolution_mm,
            settings.max_voxels,
        )
        if was_clamped:
            events.append(
                self._event(
                    tick=0,
                    motion_index=0,
                    severity=SimulationSeverity.WARNING,
                    code=SimulationCode.RESOLUTION_CLAMPED,
                    message=(
                        f"requested resolution {settings.voxel_resolution_mm:g} mm exceeds the voxel budget; "
                        f"using {used_resolution:.3f} mm"
                    ),
                    position=Position3(),
                )
            )
        settings = settings.model_copy(update={"voxel_resolution_mm": used_resolution})

        fixtures = _effective_fixtures(project)
        machine = self._catalogs.machine(project.machine_id)
        stock_top = stock_top_z(project.stock, project.wcs)
        margin = max(settings.collision_margin_mm, used_resolution)

        timeline: list[SimulationTick] = []
        checkpoints: list[VoxelCheckpoint] = []
        checkpoint_bytes = int(grid.occupied.nbytes)
        tick = 0
        motion_index = 0
        current: Position3 | None = None
        total_removed_mm3 = 0.0
        collision_count = 0

        total_motions = _count_motions(plan)
        stride = max(1, math.ceil(total_motions / settings.timeline_max_ticks)) if total_motions else 1

        for result in plan.operations:
            if result.status is OperationPlanStatus.SKIPPED_DISABLED:
                events.append(
                    self._event(
                        tick,
                        motion_index,
                        SimulationSeverity.INFO,
                        SimulationCode.OPERATION_SKIPPED,
                        f"operation {result.operation_id} is disabled; skipped",
                        current or Position3(),
                        result.operation_id,
                    )
                )
                accumulator.operations_skipped += 1
                continue
            if result.status is OperationPlanStatus.FAILED or result.program is None:
                events.append(
                    self._event(
                        tick,
                        motion_index,
                        SimulationSeverity.ERROR,
                        SimulationCode.OPERATION_SKIPPED,
                        f"operation {result.operation_id} failed during planning; skipped",
                        current or Position3(),
                        result.operation_id,
                    )
                )
                accumulator.operations_skipped += 1
                continue

            operation = next(candidate for candidate in project.operations if candidate.id == result.operation_id)
            tool = self._catalogs.tool(operation.tool_id)
            assembly = ToolAssembly(tool, machine)
            warning = assembly.unknown_geometry_warning()
            if warning is not None:
                events.append(
                    self._event(
                        tick,
                        motion_index,
                        SimulationSeverity.WARNING,
                        SimulationCode.TOOL_UNKNOWN_GEOMETRY,
                        warning,
                        current or Position3(),
                        operation.id,
                    )
                )
            accumulator.operations_simulated += 1
            outside_reported = False

            for motion in result.program.motions:
                if current is None:
                    current = motion.endpoint
                    tick += 1
                    motion_index += 1
                    continue

                if motion.kind is MotionKind.DWELL:
                    accumulator.duration_estimate_s += motion.dwell_seconds or 0.0
                else:
                    if _is_cut(motion):
                        removed = remove_motion(grid, current, motion, assembly.cutter_body(), used_resolution)
                        accumulator.removed_voxels += removed
                        total_removed_mm3 = grid.removed_voxels * (used_resolution**3)
                        depth = stock_top - motion.endpoint.z_mm
                        if depth > accumulator.max_depth_reached_mm:
                            accumulator.max_depth_reached_mm = depth
                        hit = check_reach(depth, assembly, margin)
                        if hit is not None:
                            events.append(
                                self._event(
                                    tick,
                                    motion_index,
                                    SimulationSeverity.ERROR,
                                    hit.code,
                                    hit.message,
                                    hit.position,
                                    operation.id,
                                )
                            )
                            collision_count += 1
                        accumulator.duration_estimate_s += _motion_duration(current, motion)
                        collision_count += _append_checks(
                            events, grid, assembly, fixtures, motion.endpoint, margin, tick, motion_index, operation.id, project.stock, project.wcs
                        )
                    else:
                        # Rapids never remove material; sample the path for collisions.
                        for sample in _subdivide(current, motion, used_resolution)[1:]:
                            collision_count += _append_checks(
                                events, grid, assembly, fixtures, sample, margin, tick, motion_index, operation.id, project.stock, project.wcs
                            )
                    work_area_hit = check_tip_in_work_area(motion.endpoint, machine, margin)
                    if work_area_hit is not None and not outside_reported:
                        outside_reported = True
                        events.append(
                            self._event(
                                tick,
                                motion_index,
                                SimulationSeverity.WARNING,
                                work_area_hit.code,
                                work_area_hit.message,
                                work_area_hit.position,
                                operation.id,
                            )
                        )

                current = motion.endpoint
                if progress_callback is not None and motion_index % progress_every == 0:
                    progress_callback(
                        motion_index,
                        current,
                        grid.removed_voxels,
                        grid.occupied.copy() if motion_index % mesh_every == 0 else None,
                    )
                if tick % stride == 0 or motion_index == total_motions - 1:
                    timeline.append(
                        SimulationTick(
                            index=tick,
                            motion_index=motion_index,
                            tool_position=current,
                            removed_voxels=grid.removed_voxels,
                            total_removed_mm3=total_removed_mm3,
                            collision_count=collision_count,
                            event_indices=tuple(index for index, event in enumerate(events) if event.tick == tick),
                        )
                    )
                if tick % settings.checkpoint_every == 0:
                    if (len(checkpoints) + 1) * checkpoint_bytes <= settings.max_checkpoint_bytes:
                        checkpoints.append(
                            VoxelCheckpoint(tick_index=tick, motion_index=motion_index, mask=grid.occupied.copy())
                        )
                tick += 1
                motion_index += 1

        report = SimulationReport(
            project_id=project.id,
            plan_fingerprint=plan.fingerprint(),
            settings=settings,
            events=tuple(events),
            timeline=tuple(timeline),
            stats=accumulator.build(grid),
        )
        return SimulationResult(report=report, checkpoints=tuple(checkpoints))

    @staticmethod
    def _event(
        tick: int,
        motion_index: int,
        severity: SimulationSeverity,
        code: SimulationCode,
        message: str,
        position: Position3,
        operation_id: str | None = None,
    ) -> SimulationEvent:
        return SimulationEvent(
            tick=tick,
            motion_index=motion_index,
            severity=severity,
            code=code,
            message=message,
            position=position,
            operation_id=operation_id,
        )

    def replay_mask_at(
        self,
        project: Project,
        plan: ToolpathPlan,
        settings: SimulationSettings,
        checkpoints: tuple[VoxelCheckpoint, ...],
        target_motion_index: int,
    ) -> np.ndarray:
        """Reconstruct the occupancy mask at one flat motion index.

        Restores the nearest checkpoint at or before the target, then replays
        only the sweep removals (no events/collisions) up to the target.  Used
        by the UI for scrubbing; deterministic and allocation-bounded.
        """
        grid, _used, _clamped = VoxelGrid.from_stock(
            project.stock,
            project.wcs,
            settings.voxel_resolution_mm,
            settings.max_voxels,
        )
        base = next(
            (candidate for candidate in reversed(checkpoints) if candidate.motion_index <= target_motion_index),
            None,
        )
        if base is not None:
            grid.occupied[:] = base.mask
            if base.motion_index == target_motion_index:
                return grid.occupied
            start_index = base.motion_index + 1
        else:
            start_index = 0

        machine = self._catalogs.machine(project.machine_id)
        current: Position3 | None = None
        flat = 0
        for result in plan.operations:
            if result.status is not OperationPlanStatus.SUCCEEDED or result.program is None:
                continue
            operation = next(candidate for candidate in project.operations if candidate.id == result.operation_id)
            assembly = ToolAssembly(self._catalogs.tool(operation.tool_id), machine)
            for motion in result.program.motions:
                if flat > target_motion_index:
                    return grid.occupied
                if current is None:
                    current = motion.endpoint
                    flat += 1
                    continue
                if flat > start_index and _is_cut(motion):
                    remove_motion(grid, current, motion, assembly.cutter_body(), grid.voxel_size)
                current = motion.endpoint
                flat += 1
        return grid.occupied


def _is_cut(motion: MotionCommand) -> bool:
    return motion.kind in {MotionKind.CUT_LINEAR, MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW}


def _count_motions(plan: ToolpathPlan) -> int:
    return sum(len(result.program.motions) for result in plan.operations if result.program is not None)


def _motion_duration(start: Position3, motion: MotionCommand) -> float:
    """Estimated cut duration in seconds for one motion."""
    feed = motion.feed_mm_min
    if feed is None or feed <= 0:
        return 0.0
    length = _path_length(start, motion)
    return length / feed * 60.0


def _path_length(start: Position3, motion: MotionCommand) -> float:
    dx = motion.endpoint.x_mm - start.x_mm
    dy = motion.endpoint.y_mm - start.y_mm
    dz = motion.endpoint.z_mm - start.z_mm
    if motion.kind is MotionKind.CUT_LINEAR:
        return math.sqrt(dx * dx + dy * dy + dz * dz)
    if motion.kind in {MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW}:
        assert motion.arc_center_xy is not None
        radius = math.hypot(start.x_mm - motion.arc_center_xy[0], start.y_mm - motion.arc_center_xy[1])
        start_angle = math.atan2(start.y_mm - motion.arc_center_xy[1], start.x_mm - motion.arc_center_xy[0])
        end_angle = math.atan2(
            motion.endpoint.y_mm - motion.arc_center_xy[1], motion.endpoint.x_mm - motion.arc_center_xy[0]
        )
        if motion.kind is MotionKind.CUT_ARC_CCW:
            sweep = (end_angle - start_angle) % (2.0 * math.pi)
        else:
            sweep = -((start_angle - end_angle) % (2.0 * math.pi))
        if radius > 1e-9 and math.hypot(motion.endpoint.x_mm - start.x_mm, motion.endpoint.y_mm - start.y_mm) <= 1e-6:
            sweep = 2.0 * math.pi if motion.kind is MotionKind.CUT_ARC_CCW else -2.0 * math.pi
        return abs(radius * sweep)
    return 0.0


def _effective_fixtures(project: Project) -> tuple[Fixture, ...]:
    """Return fixtures with positions relative to stock minimum corner.

    The collision module now computes absolute positions from stock-relative offsets.
    This function returns the original fixtures unchanged.
    """
    return project.fixtures


def _append_checks(
    events: list[SimulationEvent],
    grid: VoxelGrid,
    assembly: ToolAssembly,
    fixtures: tuple[Fixture, ...],
    tip: Position3,
    margin: float,
    tick: int,
    motion_index: int,
    operation_id: str,
    project_stock: Stock,
    project_wcs: WorkCoordinateSystem,
) -> int:
    """Append collision events for one tool position; returns how many."""
    count = 0
    for hit in check_collisions(grid, assembly, tip, fixtures, margin, project_stock, project_wcs):
        severity = (
            SimulationSeverity.CRITICAL
            if hit.code in {SimulationCode.COLLISION_FIXTURE, SimulationCode.COLLISION_STOCK_MATERIAL}
            else SimulationSeverity.WARNING
        )
        events.append(
            SimulationEvent(
                tick=tick,
                motion_index=motion_index,
                severity=severity,
                code=hit.code,
                message=hit.message,
                position=hit.position,
                operation_id=operation_id,
            )
        )
        count += 1
    return count


__all__ = ["SimulationResult", "Simulator", "VoxelCheckpoint"]


class _StatsAccumulator:
    """Mutable stats during simulation; frozen SimulationStats built at the end."""

    __slots__ = (
        "removed_voxels",
        "max_depth_reached_mm",
        "collision_count",
        "duration_estimate_s",
        "operations_simulated",
        "operations_skipped",
    )

    def __init__(self) -> None:
        self.removed_voxels = 0
        self.max_depth_reached_mm = 0.0
        self.collision_count = 0
        self.duration_estimate_s = 0.0
        self.operations_simulated = 0
        self.operations_skipped = 0

    def build(self, grid: VoxelGrid | None) -> SimulationStats:
        removed = grid.removed_voxels if grid is not None else 0
        voxel_size = grid.voxel_size if grid is not None else 0.0
        return SimulationStats(
            initial_voxels=int(np.prod(grid.shape)) if grid is not None else 0,
            removed_voxels=removed,
            removed_mm3=removed * (voxel_size**3),
            max_depth_reached_mm=self.max_depth_reached_mm,
            collision_count=self.collision_count,
            duration_estimate_s=self.duration_estimate_s,
            operations_simulated=self.operations_simulated,
            operations_skipped=self.operations_skipped,
        )
