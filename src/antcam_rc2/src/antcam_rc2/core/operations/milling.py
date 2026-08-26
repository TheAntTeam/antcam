"""Milling strategies: facing, roughing, pocketing, profiling, slotting, t-slotting, face_top."""

from __future__ import annotations

from antcam_rc2.core.errors import OperationError
from antcam_rc2.core.geometry.ops import contains_point, offset
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.operations.contracts import PlanningContext, StrategyResult
from antcam_rc2.core.operations.params import (
    FaceTopParameters,
    FacingParameters,
    PocketParameters,
    ProfileParameters,
    RoughingParameters,
    SlottingParameters,
    TSlotParameters,
)
from antcam_rc2.core.operations.shared import ContourTracer, closed_contours
from antcam_rc2.core.toolpath.builder import MotionBuilder
from antcam_rc2.core.toolpath.compensation import (
    facing_region_offset,
    pocket_offsets,
    profile_offset,
    slot_geometry,
)
from antcam_rc2.core.toolpath.diagnostics import ToolpathCode

_RAMP_LENGTH_MM = 2.0


class ProfileStrategy:
    """Profile/contour tracing with tool radius compensation.

    The tool centre follows the loop offset outward (``side="outside"``) or
    inward (``side="inside"``).  A ramp entry is used only when the approach
    point lies inside the original region; otherwise it degrades to a vertical
    plunge with an explicit ``entry_degraded`` warning.
    """

    def generate(self, context: PlanningContext[ProfileParameters]) -> StrategyResult:
        loops: list = []
        for entity in context.entities:
            for contour in closed_contours(entity):
                compensated = profile_offset(contour, context.tool, context.params.side)
                if compensated is None:
                    raise OperationError(
                        "profile offset degenerated for the selected tool", code=ToolpathCode.OFFSET_FAILED.value
                    )
                loops.append(compensated)
        if not loops:
            raise OperationError("profiling requires at least one closed contour")

        ramp_enabled = _verify_ramp_space(context)
        builder = MotionBuilder(context.operation.id)
        tracer = ContourTracer(
            builder=builder,
            operation_id=context.operation.id,
            plunge_feed_mm_min=context.feeds_speeds.plunge_feed_mm_min,
            cut_feed_mm_min=context.feeds_speeds.cut_feed_mm_min,
            clearance_z_mm=context.clearance_z_mm,
            tolerance_mm=context.tolerance_mm,
            ramp_enabled=ramp_enabled,
        )
        if not ramp_enabled:
            tracer.add_warning("ramp entry not feasible; degraded to vertical plunge")

        for pass_index, depth_z_mm in enumerate(context.depth_passes_z_mm):
            builder.set_pass_index(pass_index)
            tracer.trace_loops(loops, depth_z_mm)
        return tracer.finish()


class FaceTopStrategy:
    """Level the stock top by removing the configured stock allowance.

    The operation rasterizes the stock outline: ``depth_mm`` or
    ``stock_allowance_mm`` determines how much material is removed from the
    top face.  With neither set the operation is a no-op and is rejected.
    """

    def generate(self, context: PlanningContext[FaceTopParameters]) -> StrategyResult:
        allowance = context.operation.parameters.stock_allowance_mm
        if context.operation.parameters.depth_mm is None and allowance <= 0:
            raise OperationError(
                "face_top requires depth_mm or a positive stock_allowance_mm",
                code=ToolpathCode.OPERATION_PARAM_INVALID.value,
            )
        stepover = context.operation.parameters.stepover_mm or context.feeds_speeds.stepover_mm
        if stepover is None or stepover <= 0:
            raise OperationError("face_top requires a positive stepover_mm", code=ToolpathCode.STEPOVER_ZERO.value)

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
                raster = facing_region_offset(contour, context.tool, stepover)
                if not raster:
                    raise OperationError("face_top region is too small for the selected tool")
                for pass_index, depth_z_mm in enumerate(context.depth_passes_z_mm):
                    builder.set_pass_index(pass_index)
                    tracer.trace_polylines([loop.to_polyline(context.tolerance_mm) for loop in raster], depth_z_mm)
        return tracer.finish()


class FacingStrategy:
    """Face a selected region to the target depth with raster passes."""

    def generate(self, context: PlanningContext[FacingParameters]) -> StrategyResult:
        stepover = context.operation.parameters.stepover_mm or context.feeds_speeds.stepover_mm
        if stepover is None or stepover <= 0:
            raise OperationError("facing requires a positive stepover_mm", code=ToolpathCode.STEPOVER_ZERO.value)

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
                raster = facing_region_offset(contour, context.tool, stepover)
                if not raster:
                    raise OperationError("facing region is too small for the selected tool")
                for pass_index, depth_z_mm in enumerate(context.depth_passes_z_mm):
                    builder.set_pass_index(pass_index)
                    tracer.trace_polylines([loop.to_polyline(context.tolerance_mm) for loop in raster], depth_z_mm)
        return tracer.finish()


class PocketStrategy:
    """Pocket clearing with inward offset loops."""

    def generate(self, context: PlanningContext[PocketParameters]) -> StrategyResult:
        stepover = context.operation.parameters.stepover_mm or context.feeds_speeds.stepover_mm
        if stepover is None or stepover <= 0:
            raise OperationError(
                "pocket planning requires a positive stepover_mm", code=ToolpathCode.STEPOVER_ZERO.value
            )

        loops: list = []
        for entity in context.entities:
            for contour in closed_contours(entity):
                loops.extend(pocket_offsets(contour, context.tool, stepover))
        if not loops:
            raise OperationError(
                "pocket geometry is too small for the selected tool", code=ToolpathCode.GEOMETRY_TOO_SMALL.value
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
            tracer.trace_loops(loops, depth_z_mm)
        return tracer.finish()


class RoughingStrategy:
    """Generic region-based roughing with stock allowance."""

    def generate(self, context: PlanningContext[RoughingParameters]) -> StrategyResult:
        stepover = context.operation.parameters.stepover_mm or context.feeds_speeds.stepover_mm
        if stepover is None or stepover <= 0:
            raise OperationError("roughing requires a positive stepover_mm", code=ToolpathCode.STEPOVER_ZERO.value)
        allowance = context.operation.parameters.stock_allowance_mm

        loops: list = []
        for entity in context.entities:
            for contour in closed_contours(entity):
                source = contour
                if allowance > 0:
                    expanded = _expand_with_allowance(contour, allowance)
                    source = expanded or contour
                loops.extend(pocket_offsets(source, context.tool, stepover))
        if not loops:
            raise OperationError(
                "roughing geometry is too small for the selected tool", code=ToolpathCode.GEOMETRY_TOO_SMALL.value
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
            tracer.trace_loops(loops, depth_z_mm)
        return tracer.finish()


class SlottingStrategy:
    """Slot cutting along the slot axis: centerline or multi-lane raster."""

    def generate(self, context: PlanningContext[SlottingParameters]) -> StrategyResult:
        stepover = context.operation.parameters.stepover_mm or context.feeds_speeds.stepover_mm
        if stepover is None or stepover <= 0:
            raise OperationError("slotting requires a positive stepover_mm", code=ToolpathCode.STEPOVER_ZERO.value)

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
                lanes = slot_geometry(contour, context.tool, stepover)
                for pass_index, depth_z_mm in enumerate(context.depth_passes_z_mm):
                    builder.set_pass_index(pass_index)
                    tracer.trace_polylines(lanes, depth_z_mm)
        return tracer.finish()


class TSlottingStrategy:
    """T-slot cutting with a T-slot cutter along the slot axis.

    A T-slot cutter cuts its neck slot at full depth in a single pass per
    depth level; the wider head is engaged by the cutter geometry itself, so
    the tool-centre path is the slot centreline.
    """

    def generate(self, context: PlanningContext[TSlotParameters]) -> StrategyResult:
        stepover = context.operation.parameters.stepover_mm or context.feeds_speeds.stepover_mm
        if stepover is None or stepover <= 0:
            raise OperationError("t_slotting requires a positive stepover_mm", code=ToolpathCode.STEPOVER_ZERO.value)

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
                lanes = slot_geometry(contour, context.tool, stepover)
                for pass_index, depth_z_mm in enumerate(context.depth_passes_z_mm):
                    builder.set_pass_index(pass_index)
                    tracer.trace_polylines(lanes, depth_z_mm)
        return tracer.finish()


def _verify_ramp_space(context: PlanningContext) -> bool:
    """True when every source loop leaves 2 mm of -X approach room inside it."""
    if getattr(context.params, "entry_mode", "plunge") != "ramp":
        return False
    for entity in context.entities:
        for contour in closed_contours(entity):
            start = contour.start
            approach = Point2(start.x - _RAMP_LENGTH_MM, start.y)
            if not contains_point(contour, approach):
                return False
    return True


def _expand_with_allowance(contour, allowance_mm: float):
    """Expand a contour outward by the stock allowance."""
    try:
        results = offset(contour, allowance_mm)
    except Exception:
        return None
    return results[0] if results else None


__all__ = [
    "FaceTopStrategy",
    "FacingStrategy",
    "PocketStrategy",
    "ProfileStrategy",
    "RoughingStrategy",
    "SlottingStrategy",
    "TSlottingStrategy",
]
