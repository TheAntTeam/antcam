"""Drilling strategies: holes, drill, boring, hole_pocketing, thread_milling, tapping."""

from __future__ import annotations

import math

from antcam_rc2.core.errors import OperationError
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.operations.contracts import PlanningContext, StrategyResult
from antcam_rc2.core.operations.params import (
    BoringParameters,
    DrillParameters,
    HolePocketingParameters,
    HolesParameters,
    TappingParameters,
    ThreadMillingParameters,
)
from antcam_rc2.core.operations.shared import drill_centers
from antcam_rc2.core.toolpath.builder import MotionBuilder
from antcam_rc2.core.toolpath.diagnostics import ToolpathCode

_ARCS_PER_REV = 8  # 45-degree arcs per full helical revolution


class DrillStrategy:
    """Simple drilling cycle: rapid -> plunge -> retract."""

    def generate(self, context: PlanningContext[DrillParameters]) -> StrategyResult:
        builder = MotionBuilder(context.operation.id)
        _emit_drill_cycle(builder, drill_centers(context.entities), context)
        if builder.motion_count == 0:
            raise OperationError("drilling operation has no usable center geometry")
        return StrategyResult(program=builder.build())


class BoringStrategy:
    """Boring: plunge, dwell at the bottom, retract."""

    def generate(self, context: PlanningContext[BoringParameters]) -> StrategyResult:
        builder = MotionBuilder(context.operation.id)
        _emit_bore_cycle(builder, drill_centers(context.entities), context, context.params.dwell_seconds)
        if builder.motion_count == 0:
            raise OperationError("boring operation has no usable center geometry")
        return StrategyResult(program=builder.build())


class HolesStrategy:
    """Holes orchestrates drill or bore cycles from one selection."""

    def generate(self, context: PlanningContext[HolesParameters]) -> StrategyResult:
        builder = MotionBuilder(context.operation.id)
        if context.params.hole_type == "drill":
            _emit_drill_cycle(builder, drill_centers(context.entities), context)
        else:
            _emit_bore_cycle(builder, drill_centers(context.entities), context, context.params.dwell_seconds)
        if builder.motion_count == 0:
            raise OperationError("holes operation has no usable center geometry")
        return StrategyResult(program=builder.build())


class HolePocketingStrategy:
    """Hole pocketing: concentric circular clearing to ``hole_diameter_mm``.

    Each pass clears the hole as concentric full circles spaced by stepover,
    emitted as G2/G3-compatible arc motions.
    """

    def generate(self, context: PlanningContext[HolePocketingParameters]) -> StrategyResult:
        centers = drill_centers(context.entities)
        stepover = context.operation.parameters.stepover_mm or context.feeds_speeds.stepover_mm
        if stepover is None or stepover <= 0:
            raise OperationError(
                "hole_pocketing requires a positive stepover_mm", code=ToolpathCode.STEPOVER_ZERO.value
            )
        tool_radius = context.tool.cutting_diameter_mm / 2.0
        hole_radius = context.params.hole_diameter_mm / 2.0
        if hole_radius <= tool_radius:
            raise OperationError(
                "hole_pocketing requires a hole larger than the tool diameter", code=ToolpathCode.TOOL_TOO_LARGE.value
            )

        builder = MotionBuilder(context.operation.id)
        for center in centers:
            radii: list[float] = []
            radius = tool_radius
            while radius < hole_radius - 1e-9:
                radii.append(radius)
                radius += stepover
            if not radii:
                raise OperationError(
                    "hole_pocketing has no room for any clearing pass", code=ToolpathCode.GEOMETRY_TOO_SMALL.value
                )
            for pass_index, depth_z_mm in enumerate(context.depth_passes_z_mm):
                builder.set_pass_index(pass_index)
                for r in radii:
                    start_x = center.x + r
                    builder.rapid(start_x, center.y, context.clearance_z_mm)
                    builder.cut_linear(start_x, center.y, depth_z_mm, context.feeds_speeds.plunge_feed_mm_min)
                    builder.cut_arc_ccw(
                        start_x,
                        center.y,
                        depth_z_mm,
                        center.x,
                        center.y,
                        context.feeds_speeds.cut_feed_mm_min,
                    )
                    builder.rapid(start_x, center.y, context.clearance_z_mm)
        if builder.motion_count == 0:
            raise OperationError("hole_pocketing produced no motions")
        return StrategyResult(program=builder.build())


class ThreadMillingStrategy:
    """Thread milling: helical interpolation for internal threads.

    The tool plunges to the deepest pass and helical-interpolates out (or in)
    over the full threaded depth using arc segments — one revolution advances
    the tool by exactly one pitch.
    """

    def generate(self, context: PlanningContext[ThreadMillingParameters]) -> StrategyResult:
        centers = drill_centers(context.entities)
        pitch = context.params.pitch_mm
        thread_radius = context.params.thread_diameter_mm / 2.0
        tool_radius = context.tool.cutting_diameter_mm / 2.0
        if tool_radius >= thread_radius:
            raise OperationError(
                "thread_milling tool is too large for the thread diameter", code=ToolpathCode.TOOL_TOO_LARGE.value
            )

        # Thread milling is a single synchronized pass at the full depth.
        target_z = context.depth_passes_z_mm[-1]
        thread_depth = context.top_z_mm - target_z
        if thread_depth <= 0:
            raise OperationError(
                "thread_milling requires a positive threaded depth", code=ToolpathCode.OPERATION_PARAM_INVALID.value
            )

        builder = MotionBuilder(context.operation.id)
        for center in centers:
            start_x = center.x + thread_radius
            builder.rapid(start_x, center.y, context.clearance_z_mm)
            builder.cut_linear(start_x, center.y, target_z, context.feeds_speeds.plunge_feed_mm_min)
            if context.params.direction == "up":
                _emit_helix(builder, center, thread_radius, target_z, context.top_z_mm, pitch, context, ccw=True)
            else:
                _emit_helix(builder, center, thread_radius, context.top_z_mm, target_z, pitch, context, ccw=False)
            builder.rapid(start_x, center.y, context.clearance_z_mm)
        if builder.motion_count == 0:
            raise OperationError("thread_milling produced no motions")
        return StrategyResult(program=builder.build())


class TappingStrategy:
    """Rigid tapping: synchronized plunge/retract at ``feed = rpm * pitch``.

    The machine capability gate (``rigid_tapping``) is enforced by
    ``FeedsSpeedsCalculator`` and the service before this strategy runs; the
    retract is emitted as a synchronized cut so the postprocessor can reverse
    the spindle.
    """

    def generate(self, context: PlanningContext[TappingParameters]) -> StrategyResult:
        centers = drill_centers(context.entities)
        builder = MotionBuilder(context.operation.id)
        target_z = context.depth_passes_z_mm[-1]
        for center in centers:
            builder.rapid(center.x, center.y, context.clearance_z_mm)
            builder.cut_linear(center.x, center.y, target_z, context.feeds_speeds.plunge_feed_mm_min)
            builder.cut_linear(center.x, center.y, context.clearance_z_mm, context.feeds_speeds.plunge_feed_mm_min)
        if builder.motion_count == 0:
            raise OperationError("tapping operation has no usable center geometry")
        return StrategyResult(program=builder.build())


def _emit_drill_cycle(builder: MotionBuilder, centers: tuple[Point2, ...], context) -> None:
    """Append rapid -> plunge -> retract for every centre at every depth pass."""
    for center in centers:
        for pass_index, depth_z_mm in enumerate(context.depth_passes_z_mm):
            builder.set_pass_index(pass_index)
            builder.rapid(center.x, center.y, context.clearance_z_mm)
            builder.cut_linear(center.x, center.y, depth_z_mm, context.feeds_speeds.plunge_feed_mm_min)
            builder.rapid(center.x, center.y, context.clearance_z_mm)


def _emit_bore_cycle(builder: MotionBuilder, centers: tuple[Point2, ...], context, dwell_seconds: float) -> None:
    """Append rapid -> plunge -> dwell -> retract for every centre and pass."""
    for center in centers:
        for pass_index, depth_z_mm in enumerate(context.depth_passes_z_mm):
            builder.set_pass_index(pass_index)
            builder.rapid(center.x, center.y, context.clearance_z_mm)
            builder.cut_linear(center.x, center.y, depth_z_mm, context.feeds_speeds.plunge_feed_mm_min)
            builder.dwell(dwell_seconds)
            builder.rapid(center.x, center.y, context.clearance_z_mm)


def _emit_helix(
    builder: MotionBuilder,
    center,
    radius: float,
    start_z: float,
    end_z: float,
    pitch: float,
    context: PlanningContext,
    *,
    ccw: bool,
) -> None:
    """Emit a helical path from ``start_z`` to ``end_z`` as arc segments.

    One full revolution advances by ``pitch``; the helix is decomposed into
    ``_ARCS_PER_REV`` equal arcs per revolution.
    """
    if pitch <= 0:
        raise OperationError(
            "thread_milling requires a positive pitch_mm", code=ToolpathCode.OPERATION_PARAM_INVALID.value
        )
    travel = end_z - start_z
    if abs(travel) <= 1e-9:
        return
    direction = 1.0 if travel > 0 else -1.0
    dz_per_arc = direction * pitch / _ARCS_PER_REV
    steps = max(1, math.ceil(abs(travel) / abs(dz_per_arc)))
    angle_step = (2.0 * math.pi / _ARCS_PER_REV) * direction
    if not ccw:
        angle_step = -angle_step

    angle = 0.0
    z = start_z
    feed = context.feeds_speeds.cut_feed_mm_min
    for _ in range(steps):
        angle += angle_step
        z += dz_per_arc
        end_x = center.x + radius * math.cos(angle)
        end_y = center.y + radius * math.sin(angle)
        if ccw:
            builder.cut_arc_ccw(end_x, end_y, z, center.x, center.y, feed)
        else:
            builder.cut_arc_cw(end_x, end_y, z, center.x, center.y, feed)


__all__ = [
    "BoringStrategy",
    "DrillStrategy",
    "HolePocketingStrategy",
    "HolesStrategy",
    "TappingStrategy",
    "ThreadMillingStrategy",
]
