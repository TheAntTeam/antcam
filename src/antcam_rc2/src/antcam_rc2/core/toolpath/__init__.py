"""Neutral, deterministic 2.5D toolpath models and planning helpers."""

from antcam_rc2.core.toolpath.builder import MotionBuilder
from antcam_rc2.core.toolpath.compensation import (
    facing_region_offset,
    pocket_offsets,
    profile_offset,
    slot_centerline,
    slot_geometry,
    tool_radius_mm,
    validate_tool_fits,
)
from antcam_rc2.core.toolpath.depth_passes import plan_depth_passes
from antcam_rc2.core.toolpath.diagnostics import ToolpathCode, ToolpathSeverity, make_diagnostic
from antcam_rc2.core.toolpath.entry_exit import (
    build_entry_sequence,
    build_exit_sequence,
    build_link_motion,
    can_lead_out,
    can_ramp,
)
from antcam_rc2.core.toolpath.models import (
    MotionCommand,
    MotionKind,
    MotionProgram,
    OperationPlanStatus,
    Position3,
    ToolpathArtifact,
    ToolpathDiagnostic,
    ToolpathOperationResult,
    ToolpathPlan,
)
from antcam_rc2.core.toolpath.ordering import (
    contour_end_point,
    contour_start_point,
    distance_squared,
    entity_end_point,
    entity_start_point,
    order_contours_by_fingerprint,
    order_islands_nearest_neighbor,
    reorder_pass_contours,
)
from antcam_rc2.core.toolpath.settings import PlanningSettings
from antcam_rc2.core.toolpath.setup_frame import (
    clearance_z_mm,
    stock_bottom_z_mm,
    stock_center_xy_mm,
    stock_corner_xy_mm,
    stock_top_z_mm,
    target_z_mm,
)

__all__ = [
    "MotionBuilder",
    "facing_region_offset",
    "pocket_offsets",
    "profile_offset",
    "slot_centerline",
    "slot_geometry",
    "tool_radius_mm",
    "validate_tool_fits",
    "plan_depth_passes",
    "ToolpathCode",
    "ToolpathSeverity",
    "make_diagnostic",
    "build_entry_sequence",
    "build_exit_sequence",
    "build_link_motion",
    "can_lead_out",
    "can_ramp",
    "MotionCommand",
    "MotionKind",
    "MotionProgram",
    "OperationPlanStatus",
    "Position3",
    "ToolpathDiagnostic",
    "ToolpathArtifact",
    "ToolpathOperationResult",
    "ToolpathPlan",
    "PlanningSettings",
    "contour_end_point",
    "contour_start_point",
    "distance_squared",
    "entity_end_point",
    "entity_start_point",
    "order_contours_by_fingerprint",
    "order_islands_nearest_neighbor",
    "reorder_pass_contours",
    "clearance_z_mm",
    "stock_bottom_z_mm",
    "stock_center_xy_mm",
    "stock_corner_xy_mm",
    "stock_top_z_mm",
    "target_z_mm",
]
