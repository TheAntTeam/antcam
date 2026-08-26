"""Carving strategies: V-carve roughing, V-carving, engraving, chamfering, filleting."""

from __future__ import annotations

import math

from antcam_rc2.core.errors import OperationError
from antcam_rc2.core.operations.contracts import PlanningContext, StrategyResult
from antcam_rc2.core.operations.params import (
    ChamferingParameters,
    EngravingParameters,
    FilletingParameters,
    VCarveRoughingParameters,
    VCarvingParameters,
)
from antcam_rc2.core.operations.shared import ContourTracer, closed_contours, entity_polylines
from antcam_rc2.core.toolpath.builder import MotionBuilder
from antcam_rc2.core.toolpath.compensation import pocket_offsets
from antcam_rc2.core.toolpath.diagnostics import ToolpathCode


class VCarveRoughingStrategy:
    """Clear a V-groove with an end mill, leaving allowance for the V-bit finish."""

    def generate(self, context: PlanningContext[VCarveRoughingParameters]) -> StrategyResult:
        stepover = context.operation.parameters.stepover_mm or context.feeds_speeds.stepover_mm
        if stepover is None or stepover <= 0:
            raise OperationError(
                "v_carve_roughing requires a positive stepover_mm", code=ToolpathCode.STEPOVER_ZERO.value
            )

        # The end mill clears down to the depth of the V-groove minus the finish allowance.
        v_depth = _v_groove_depth(context.params.angle_deg, context.params.width_mm)
        allowance = context.operation.parameters.stock_allowance_mm or 0.1
        rough_limit_z = context.top_z_mm - max(0.0, v_depth - allowance)

        loops: list = []
        for entity in context.entities:
            for contour in closed_contours(entity):
                loops.extend(pocket_offsets(contour, context.tool, stepover))
        if not loops:
            raise OperationError(
                "v_carve_roughing geometry is too small for the selected tool",
                code=ToolpathCode.GEOMETRY_TOO_SMALL.value,
            )

        builder = MotionBuilder(context.operation.id)
        tracer = ContourTracer(
            builder=builder,
            operation_id=context.operation.id,
            plunge_feed_mm_min=context.feeds_speeds.plunge_feed_mm_min,
            cut_feed_mm_min=context.feeds_speeds.cut_feed_mm_min,
            clearance_z_mm=context.clearance_z_mm,
            tolerance_mm=context.tolerance_mm,
        )
        for pass_index, depth_z_mm in enumerate(context.depth_passes_z_mm):
            builder.set_pass_index(pass_index)
            tracer.trace_loops(loops, max(depth_z_mm, rough_limit_z))
        return tracer.finish()


class VCarvingStrategy:
    """V-carving: follows centerlines with a V-bit at the width-derived depth.

    The cut depth per pass is the shallower of the planned pass depth and the
    depth implied by the V-groove width, so the tool never exceeds the
    requested surface width.
    """

    def generate(self, context: PlanningContext[VCarvingParameters]) -> StrategyResult:
        groove_depth = _v_groove_depth(context.params.angle_deg, context.params.width_mm)
        groove_limit_z = context.top_z_mm - groove_depth

        builder = MotionBuilder(context.operation.id)
        tracer = ContourTracer(
            builder=builder,
            operation_id=context.operation.id,
            plunge_feed_mm_min=context.feeds_speeds.plunge_feed_mm_min,
            cut_feed_mm_min=context.feeds_speeds.cut_feed_mm_min,
            clearance_z_mm=context.clearance_z_mm,
            tolerance_mm=context.tolerance_mm,
        )
        for entity in context.entities:
            polylines = entity_polylines(entity, context.tolerance_mm)
            for pass_index, depth_z_mm in enumerate(context.depth_passes_z_mm):
                builder.set_pass_index(pass_index)
                tracer.trace_polylines(polylines, max(depth_z_mm, groove_limit_z))
        return tracer.finish()


class EngravingStrategy:
    """Engraving: follows centerlines with a V-bit or small cutter at pass depth."""

    def generate(self, context: PlanningContext[EngravingParameters]) -> StrategyResult:
        builder = MotionBuilder(context.operation.id)
        tracer = ContourTracer(
            builder=builder,
            operation_id=context.operation.id,
            plunge_feed_mm_min=context.feeds_speeds.plunge_feed_mm_min,
            cut_feed_mm_min=context.feeds_speeds.cut_feed_mm_min,
            clearance_z_mm=context.clearance_z_mm,
            tolerance_mm=context.tolerance_mm,
        )
        for entity in context.entities:
            polylines = entity_polylines(entity, context.tolerance_mm)
            for pass_index, depth_z_mm in enumerate(context.depth_passes_z_mm):
                builder.set_pass_index(pass_index)
                tracer.trace_polylines(polylines, depth_z_mm)
        return tracer.finish()


class ChamferingStrategy:
    """Chamfering: traces edges at the depth derived from chamfer width/angle.

    The cut depth per pass is the shallower of the planned pass depth and the
    depth implied by the chamfer width, so the edge is never over-cut.
    """

    def generate(self, context: PlanningContext[ChamferingParameters]) -> StrategyResult:
        chamfer_depth = context.params.width_mm / math.tan(math.radians(context.params.angle_deg))
        chamfer_limit_z = context.top_z_mm - chamfer_depth

        builder = MotionBuilder(context.operation.id)
        tracer = ContourTracer(
            builder=builder,
            operation_id=context.operation.id,
            plunge_feed_mm_min=context.feeds_speeds.plunge_feed_mm_min,
            cut_feed_mm_min=context.feeds_speeds.cut_feed_mm_min,
            clearance_z_mm=context.clearance_z_mm,
            tolerance_mm=context.tolerance_mm,
        )
        for entity in context.entities:
            for contour in closed_contours(entity):
                points = contour.to_polyline(context.tolerance_mm)
                for pass_index, depth_z_mm in enumerate(context.depth_passes_z_mm):
                    builder.set_pass_index(pass_index)
                    tracer.trace_polylines([points], max(depth_z_mm, chamfer_limit_z))
        return tracer.finish()


class FilletingStrategy:
    """Filleting: requires a 3D toolpath capability not available in 2.5D.

    The registry declares ``3d_toolpath`` as a required machine capability, so
    this strategy is never reached on machines that lack it; the guard below
    keeps the failure explicit and safe even for misconfigured registries.
    """

    def generate(self, context: PlanningContext[FilletingParameters]) -> StrategyResult:
        raise OperationError(
            "filleting requires 3D toolpath capability (not available in the 2.5D engine)",
            code=ToolpathCode.MACHINE_CAPABILITY_MISSING.value,
        )


def _v_groove_depth(angle_deg: float, width_mm: float) -> float:
    """Depth of a V-groove of ``width_mm`` at the surface for a given tool angle."""
    return (width_mm / 2.0) / math.tan(math.radians(angle_deg) / 2.0)


__all__ = [
    "ChamferingStrategy",
    "EngravingStrategy",
    "FilletingStrategy",
    "VCarveRoughingStrategy",
    "VCarvingStrategy",
]
