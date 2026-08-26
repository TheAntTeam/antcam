"""Post-processing contracts: settings, tool table, context and G-code program.

All contracts are strict, immutable and JSON-serializable (same conventions as
the toolpath/simulation models).  A :class:`GCodeProgram` is the derived
artifact the CLI and UI export as ``.nc``; it never lives in the project
document and is fully deterministic (no timestamps).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from antcam_rc2.core.databases.models import MachineProfile
from antcam_rc2.core.project.models import Project
from antcam_rc2.core.toolpath.models import ToolpathPlan
from antcam_rc2.core.units import UnitSystem


class _PostModel(BaseModel):
    """Shared strict immutable behavior for post contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PostSettings(_PostModel):
    """Explicit, deterministic post-processing options."""

    units: UnitSystem = UnitSystem.METRIC
    decimals: int = Field(default=3, ge=0, le=6)
    emit_comments: bool = True
    arc_format: Literal["ijk", "r"] = "ijk"
    tool_change_policy: Literal["auto", "none"] = "auto"
    safe_retract_z_mm: float | None = Field(default=None)
    dwell_units: Literal["s", "ms"] = "s"
    coolant_mcodes: dict[str, str | None] = Field(
        default_factory=lambda: {"flood": "M8", "mist": "M7", "aerodust": None}
    )
    merge_consecutive_rapids: bool = True
    max_feed_mm_min: float | None = Field(default=None, gt=0)
    line_ending: Literal["lf", "crlf"] = "lf"
    header_extra_lines: tuple[str, ...] = ()
    footer_extra_lines: tuple[str, ...] = ()
    program_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolEntry:
    """One deterministic tool assignment (first-use order)."""

    tool_id: str
    tool_number: int
    name: str
    diameter_mm: float


@dataclass(frozen=True, slots=True)
class ToolTable:
    """Maps catalog tool ids to stable T numbers."""

    entries: tuple[ToolEntry, ...] = ()

    def number_for(self, tool_id: str) -> int:
        for entry in self.entries:
            if entry.tool_id == tool_id:
                return entry.tool_number
        raise KeyError(f"tool not in table: {tool_id}")

    def by_number(self, tool_number: int) -> ToolEntry | None:
        for entry in self.entries:
            if entry.tool_number == tool_number:
                return entry
        return None


@dataclass(frozen=True, slots=True)
class OperationMeta:
    """Per-operation post context (rpm comes from the plan, never recomputed)."""

    operation_id: str
    tool_id: str
    name: str
    rpm: float | None
    cooling_id: str


@dataclass(frozen=True, slots=True)
class PostContext:
    """The immutable bundle consumed by one post-processor invocation."""

    project: Project
    plan: ToolpathPlan
    settings: PostSettings
    machine: MachineProfile
    tool_table: ToolTable
    operation_meta: dict[str, OperationMeta] = field(default_factory=dict)
    plan_fingerprint: str = field(default="")


class GCodeProgram(_PostModel):
    """A deterministic G-code program: ordered lines plus provenance."""

    post_id: str
    schema_version: str = "1.0"
    plan_fingerprint: str
    settings: PostSettings
    lines: tuple[str, ...] = ()
    motion_line_count: int = Field(default=0, ge=0)

    def text(self) -> str:
        """The canonical file text (no trailing blank line)."""
        ending = "\r\n" if self.settings.line_ending == "crlf" else "\n"
        return ending.join(self.lines)

    def fingerprint(self) -> str:
        """A canonical SHA-256 identity over the emitted lines."""
        payload = {"post_id": self.post_id, "lines": list(self.lines)}
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "GCodeProgram",
    "OperationMeta",
    "PostContext",
    "PostSettings",
    "ToolEntry",
    "ToolTable",
]
