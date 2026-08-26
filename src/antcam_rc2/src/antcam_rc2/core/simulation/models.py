"""Simulation domain contracts: settings, events, timeline ticks and report.

All contracts are strict, immutable and JSON-serializable (same conventions as
the toolpath models).  The report is the only persistent artifact: it never
contains the voxel mask (transient worker state), only scalars, timeline
samples and events.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from antcam_rc2.core.toolpath.models import Position3


class _SimulationModel(BaseModel):
    """Shared strict immutable behavior for simulation contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SimulationSeverity(StrEnum):
    """Severity of one simulation event."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class SimulationCode(StrEnum):
    """Stable machine-readable event codes emitted by the simulator."""

    TOOL_UNKNOWN_GEOMETRY = "tool_unknown_geometry"
    RESOLUTION_CLAMPED = "resolution_clamped"
    COLLISION_FIXTURE = "collision_fixture"
    COLLISION_STOCK_MATERIAL = "collision_stock_material"
    OUTSIDE_WORK_AREA = "outside_work_area"
    REACH_EXCEEDED = "reach_exceeded"
    PLAN_NOT_EXECUTABLE = "plan_not_executable"
    OPERATION_SKIPPED = "operation_skipped"
    GRID_LIMIT_REACHED = "grid_limit_reached"


class SimulationSettings(_SimulationModel):
    """Explicit simulation inputs; every field has a deterministic default."""

    voxel_resolution_mm: float | None = Field(default=None, gt=0)
    collision_margin_mm: float = Field(default=0.5, ge=0)
    checkpoint_every: int = Field(default=64, ge=1)
    timeline_max_ticks: int = Field(default=4096, ge=1)
    check_collisions: bool = True
    max_voxels: int = Field(default=32_000_000, ge=1_000)
    max_checkpoint_bytes: int = Field(default=67_108_864, ge=1_000_000)


class SimulationEvent(_SimulationModel):
    """One machine-readable simulation finding with full context."""

    tick: int = Field(ge=0)
    motion_index: int = Field(ge=0)
    severity: SimulationSeverity
    code: SimulationCode
    message: str = Field(min_length=1)
    position: Position3
    operation_id: str | None = None
    details: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class SimulationTick(_SimulationModel):
    """One timeline sample: cheap scalars referencing the event list."""

    index: int = Field(ge=0)
    motion_index: int = Field(ge=0)
    tool_position: Position3
    removed_voxels: int = Field(ge=0)
    total_removed_mm3: float = Field(ge=0)
    collision_count: int = Field(ge=0)
    event_indices: tuple[int, ...] = ()


class SimulationStats(_SimulationModel):
    """Aggregate statistics of one simulation run."""

    initial_voxels: int = 0
    removed_voxels: int = 0
    removed_mm3: float = 0.0
    max_depth_reached_mm: float = 0.0
    collision_count: int = 0
    duration_estimate_s: float = 0.0
    operations_simulated: int = 0
    operations_skipped: int = 0


class SimulationReport(_SimulationModel):
    """The persistent, deterministic simulation artifact."""

    schema_version: str = "1.0"
    project_id: str
    plan_fingerprint: str
    settings: SimulationSettings
    events: tuple[SimulationEvent, ...] = ()
    timeline: tuple[SimulationTick, ...] = ()
    stats: SimulationStats

    def fingerprint(self) -> str:
        """A canonical SHA-256 identity for the normalized report content."""
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "SimulationCode",
    "SimulationEvent",
    "SimulationReport",
    "SimulationSettings",
    "SimulationSeverity",
    "SimulationStats",
    "SimulationTick",
]
