"""Strict, JSON-stable neutral motion and toolpath result contracts."""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from antcam_rc2.core.feeds_speeds.models import FeedSpeedResult
from antcam_rc2.core.project.models import OperationType
from antcam_rc2.core.toolpath.settings import PlanningSettings


class _ToolpathModel(BaseModel):
    """Shared strict immutable behavior for Phase 4 toolpath contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Position3(_ToolpathModel):
    """An absolute WCS position in millimetres."""

    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0


class MotionKind(StrEnum):
    """Machine-neutral motion primitives consumed by future postprocessors."""

    RAPID = "rapid"
    CUT_LINEAR = "cut_linear"
    CUT_ARC_CW = "cut_arc_cw"
    CUT_ARC_CCW = "cut_arc_ccw"
    DWELL = "dwell"


class ToolpathSeverity(StrEnum):
    """Structured diagnostics severity."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ToolpathDiagnostic(_ToolpathModel):
    """Machine-readable planning evidence attached to a result or plan."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: ToolpathSeverity
    operation_id: str | None = None
    details: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class MotionCommand(_ToolpathModel):
    """One absolute endpoint motion with no G-code-specific state."""

    kind: MotionKind
    endpoint: Position3
    feed_mm_min: float | None = Field(default=None, gt=0)
    arc_center_xy: tuple[float, float] | None = None
    dwell_seconds: float | None = Field(default=None, gt=0)
    operation_id: str | None = None
    pass_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_motion(self) -> MotionCommand:
        is_cut = self.kind in {MotionKind.CUT_LINEAR, MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW}
        is_arc = self.kind in {MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW}
        if is_cut and self.feed_mm_min is None:
            raise ValueError("feed_mm_min is required for cutting motion")
        if not is_cut and self.feed_mm_min is not None:
            raise ValueError("feed_mm_min is only valid for cutting motion")
        if is_arc and self.arc_center_xy is None:
            raise ValueError("arc_center_xy is required for arc motion")
        if not is_arc and self.arc_center_xy is not None:
            raise ValueError("arc_center_xy is only valid for arc motion")
        if self.kind is MotionKind.DWELL and self.dwell_seconds is None:
            raise ValueError("dwell_seconds is required for dwell motion")
        if self.kind is not MotionKind.DWELL and self.dwell_seconds is not None:
            raise ValueError("dwell_seconds is only valid for dwell motion")
        return self


class MotionProgram(_ToolpathModel):
    """Ordered non-empty motion sequence for one operation result."""

    motions: tuple[MotionCommand, ...] = Field(min_length=1)


class OperationPlanStatus(StrEnum):
    """Planning outcome for one persistent project operation."""

    SUCCEEDED = "succeeded"
    SKIPPED_DISABLED = "skipped_disabled"
    FAILED = "failed"


class ToolpathOperationResult(_ToolpathModel):
    """Toolpath result and diagnostics for one operation in project order."""

    operation_id: str
    operation_type: OperationType
    status: OperationPlanStatus
    program: MotionProgram | None = None
    pass_count: int = Field(default=0, ge=0)
    feeds_speeds: FeedSpeedResult | None = None
    diagnostics: tuple[ToolpathDiagnostic, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cut_length_mm(self) -> float:
        """Total cutting distance (linear + arc) of the produced program."""
        if self.program is None:
            return 0.0
        total = 0.0
        previous: Position3 | None = None
        for motion in self.program.motions:
            if motion.kind is MotionKind.CUT_LINEAR:
                if previous is not None:
                    total += _distance_3d(previous, motion.endpoint)
            elif motion.kind in {MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW}:
                if previous is not None and motion.arc_center_xy is not None:
                    total += _arc_length(previous, motion)
            previous = motion.endpoint
        return total

    @classmethod
    def canonical_excludes(cls) -> dict:
        return {"cut_length_mm": ...}

    @model_validator(mode="after")
    def _validate_status_program(self) -> ToolpathOperationResult:
        if self.status is OperationPlanStatus.SUCCEEDED and self.program is None:
            raise ValueError("succeeded operation result requires a program")
        if self.status is not OperationPlanStatus.SUCCEEDED and self.program is not None:
            raise ValueError("non-succeeded operation result must not contain a program")
        return self


class ToolpathPlan(_ToolpathModel):
    """Project-ordered, postprocessor-neutral plan with deterministic identity."""

    schema_version: str = "1.0"
    project_id: str
    operations: tuple[ToolpathOperationResult, ...] = ()
    diagnostics: tuple[ToolpathDiagnostic, ...] = ()
    catalog_versions: dict[str, str] = Field(default_factory=dict)
    registry_versions: dict[str, str] = Field(default_factory=dict)
    settings: PlanningSettings | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_executable(self) -> bool:
        """True when at least one operation is planned and none failed."""
        return bool(self.operations) and all(
            result.status is not OperationPlanStatus.FAILED for result in self.operations
        )

    @classmethod
    def canonical_excludes(cls) -> dict:
        return {"is_executable": ..., "operations": {"__all__": {"cut_length_mm"}}}

    def fingerprint(self) -> str:
        """Return a SHA-256 identity for normalized serializable plan content."""
        payload = self.model_dump(mode="json", exclude=self.canonical_excludes())
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class ToolpathArtifact(_ToolpathModel):
    """Versioned export envelope binding a plan to its project and scene.

    An artifact is valid only when the scene fingerprint matches the project
    geometry binding; any project, catalog, settings or scene change invalidates
    it (Phase 4.11: no implicit cache, content-addressed in Phase 8).
    """

    artifact_schema_version: str = "1.0"
    project_id: str
    scene_fingerprint: str
    project_fingerprint: str
    plan: ToolpathPlan

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_valid(self) -> bool:
        return self.plan.is_executable

    @classmethod
    def canonical_excludes(cls) -> dict:
        return {"is_valid": ..., "plan": {"is_executable": ..., "operations": {"__all__": {"cut_length_mm"}}}}


def _distance_3d(a: Position3, b: Position3) -> float:
    dx = a.x_mm - b.x_mm
    dy = a.y_mm - b.y_mm
    dz = a.z_mm - b.z_mm
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _arc_length(start: Position3, motion: MotionCommand) -> float:
    """Arc length from the start point, endpoint and center."""
    assert motion.arc_center_xy is not None
    radius = math.hypot(start.x_mm - motion.arc_center_xy[0], start.y_mm - motion.arc_center_xy[1])
    start_angle = math.atan2(start.y_mm - motion.arc_center_xy[1], start.x_mm - motion.arc_center_xy[0])
    end_angle = math.atan2(
        motion.endpoint.y_mm - motion.arc_center_xy[1], motion.endpoint.x_mm - motion.arc_center_xy[0]
    )
    dx = motion.endpoint.x_mm - start.x_mm
    dy = motion.endpoint.y_mm - start.y_mm
    if math.hypot(dx, dy) <= 1e-9:
        return 2.0 * math.pi * radius  # full circle
    sweep = (end_angle - start_angle) % (2.0 * math.pi)
    if motion.kind is MotionKind.CUT_ARC_CW:
        sweep = (2.0 * math.pi - sweep) % (2.0 * math.pi)
    return radius * sweep


# Re-exported for the artifact convenience accessor used by tests and CLI.
__all__ = [
    "MotionCommand",
    "MotionKind",
    "MotionProgram",
    "OperationPlanStatus",
    "Position3",
    "ToolpathArtifact",
    "ToolpathDiagnostic",
    "ToolpathOperationResult",
    "ToolpathPlan",
    "ToolpathSeverity",
]
