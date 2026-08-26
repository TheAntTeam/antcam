"""Pure plugin contracts for registered Phase 4 operation strategies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from antcam_rc2.core.databases.models import MachineProfile, Tool, ToolType
from antcam_rc2.core.feeds_speeds.models import FeedSpeedResult, OperationFamily
from antcam_rc2.core.io.scene import SceneEntity
from antcam_rc2.core.project.models import Operation, OperationType
from antcam_rc2.core.toolpath.models import MotionProgram, ToolpathDiagnostic


class StrategyParameters(BaseModel):
    """Base schema for per-strategy JSON stored in an ``Operation``.

    Each registered operation declares its own strict, frozen subclass; the
    registry validates the persistent ``strategy_parameters`` dict against it
    before any motion is generated.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True, slots=True)
class StrategyResult:
    """A strategy outcome: the motion program plus any non-fatal diagnostics.

    Warnings (e.g. a degraded ramp entry) travel with the program so the
    service can attach them to the operation result without losing evidence.
    """

    program: MotionProgram
    diagnostics: tuple[ToolpathDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanningContext[P: StrategyParameters]:
    """All resolved dependencies consumed by one stateless strategy invocation.

    The context is fully resolved and validated by ``ToolpathService`` before
    the strategy runs; strategies are pure functions of this snapshot.  The
    ``params`` field is typed with the concrete strategy schema, so strategies
    access their validated parameters type-safely.
    """

    operation: Operation
    params: P
    entities: tuple[SceneEntity, ...]
    tool: Tool
    machine: MachineProfile
    feeds_speeds: FeedSpeedResult
    depth_passes_z_mm: tuple[float, ...]
    top_z_mm: float
    clearance_z_mm: float
    tolerance_mm: float


class ToolpathStrategy(Protocol):
    """A stateless generator that plans one validated operation context."""

    def generate(self, context: PlanningContext[StrategyParameters]) -> StrategyResult:
        """Generate a neutral motion program from an explicit context."""


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    """Metadata and factory for one persistently named operation type.

    Attributes:
        operation_type: the stable persistent vocabulary key.
        display_name: human-readable label for UIs and dumps.
        parameters_model: strict Pydantic schema for ``strategy_parameters``.
        strategy_factory: zero-argument factory for a stateless strategy.
        family: feed/speed calculation family.
        requires_depth: True when ``depth_mm`` is mandatory.
        allowed_tool_types: tool categories accepted by this operation; an
            empty frozenset means "any tool".
        required_capabilities: machine capability keys (e.g. ``rigid_tapping``,
            ``3d_toolpath``) that must be declared by the machine profile.
        version: schema version of the definition, used in plan artifacts.
    """

    operation_type: OperationType
    display_name: str
    parameters_model: type[StrategyParameters]
    strategy_factory: Callable[[], ToolpathStrategy]
    family: OperationFamily = OperationFamily.MILLING
    requires_depth: bool = True
    allowed_tool_types: frozenset[ToolType] = frozenset()
    required_capabilities: frozenset[str] = frozenset()
    version: str = "1.0"
