"""Structured diagnostic codes and helpers for Phase 4 toolpath planning."""

from __future__ import annotations

from enum import StrEnum

from antcam_rc2.core.toolpath.models import ToolpathSeverity

__all__ = ["ToolpathCode", "ToolpathSeverity", "make_diagnostic"]


class ToolpathCode(StrEnum):
    """Stable machine-readable diagnostic codes for toolpath planning."""

    GEOMETRY_BINDING_MISMATCH = "geometry_binding_mismatch"
    GEOMETRY_REF_STALE = "geometry_ref_stale"
    GEOMETRY_REF_MISSING = "geometry_ref_missing"
    GEOMETRY_UNSUPPORTED = "geometry_unsupported"
    GEOMETRY_TOO_SMALL = "geometry_too_small"
    OPERATION_DISABLED = "operation_disabled"
    OPERATION_DEPTH_REQUIRED = "operation_depth_required"
    OPERATION_PARAM_INVALID = "operation_param_invalid"
    OPERATION_NOT_REGISTERED = "operation_not_registered"
    TOOL_INCOMPATIBLE = "tool_incompatible"
    TOOL_TOO_LARGE = "tool_too_large"
    MACHINE_CAPABILITY_MISSING = "machine_capability_missing"
    MACHINE_SPINDLE_SYNC_REQUIRED = "machine_spindle_sync_required"
    STOCK_DEPTH_EXCEEDED = "stock_depth_exceeded"
    CLEARANCE_NON_POSITIVE = "clearance_non_positive"
    STEPDOWN_ZERO = "stepdown_zero"
    STEPOVER_ZERO = "stepover_zero"
    FEED_CLAMPED = "feed_clamped"
    ENTRY_DEGRADED = "entry_degraded"
    ORDER_NOT_OPTIMIZED = "order_not_optimized"
    OFFSET_FAILED = "offset_failed"
    OFFSET_SELF_INTERSECTION = "offset_self_intersection"
    PLANNING_VALIDATION_ERROR = "planning_validation_error"
    OPERATION_ERROR = "operation_error"
    CATALOG_VERSION_MISMATCH = "catalog_version_mismatch"


def make_diagnostic(
    code: ToolpathCode | str,
    message: str,
    severity: ToolpathSeverity = ToolpathSeverity.ERROR,
    operation_id: str | None = None,
    geometry_ref: str | None = None,
    details: dict[str, str | float | int | bool | None] | None = None,
) -> dict:
    """Create a diagnostic dictionary for structured error reporting."""
    return {
        "code": code if isinstance(code, str) else code.value,
        "message": message,
        "severity": severity.value if isinstance(severity, ToolpathSeverity) else severity,
        "operation_id": operation_id,
        "geometry_ref": geometry_ref,
        "details": details or {},
    }
