"""Explicit deterministic registry of operation strategy definitions."""

from __future__ import annotations

from collections.abc import Iterable

from antcam_rc2.core.databases.models import ToolType
from antcam_rc2.core.errors import OperationError
from antcam_rc2.core.feeds_speeds.models import OperationFamily
from antcam_rc2.core.operations.contracts import OperationDefinition, StrategyParameters
from antcam_rc2.core.operations.params import (
    BoringParameters,
    ChamferingParameters,
    DrillParameters,
    EngravingParameters,
    FaceTopParameters,
    FacingParameters,
    FilletingParameters,
    HolePocketingParameters,
    HolesParameters,
    PocketParameters,
    ProfileParameters,
    RoughingParameters,
    SlottingParameters,
    TappingParameters,
    ThreadMillingParameters,
    TSlotParameters,
    VCarveRoughingParameters,
    VCarvingParameters,
)
from antcam_rc2.core.project.models import OperationType

_CUTTERS = frozenset({ToolType.END_MILL, ToolType.BALL, ToolType.BULL})
_CARVERS = frozenset({ToolType.V_BIT, ToolType.END_MILL, ToolType.BALL, ToolType.BULL})


class OperationRegistry:
    """A bootstrap-owned registry that rejects ambiguous operation definitions."""

    def __init__(self, definitions: Iterable[OperationDefinition] = ()) -> None:
        self._definitions: dict[OperationType, OperationDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: OperationDefinition) -> None:
        """Register one operation definition exactly once."""
        if definition.operation_type in self._definitions:
            raise OperationError(f"operation type already registered: {definition.operation_type.value}")
        self._definitions[definition.operation_type] = definition

    def definition(self, operation_type: OperationType | str) -> OperationDefinition:
        """Return a definition or raise a stable domain error."""
        resolved = OperationType(operation_type)
        try:
            return self._definitions[resolved]
        except KeyError as exc:
            raise OperationError(f"operation type not registered: {resolved.value}") from exc

    def definitions(self) -> tuple[OperationDefinition, ...]:
        """Return definitions in persistent OperationType declaration order."""
        return tuple(
            self._definitions[operation_type] for operation_type in OperationType if operation_type in self._definitions
        )

    def operation_types(self) -> tuple[OperationType, ...]:
        """Return registered types in canonical declaration order."""
        return tuple(definition.operation_type for definition in self.definitions())

    def definition_versions(self) -> dict[str, str]:
        """Return ``operation_type -> definition version`` in stable order."""
        return {definition.operation_type.value: definition.version for definition in self.definitions()}


def _milling_definition(
    operation_type: OperationType,
    parameters_model: type[StrategyParameters],
    strategy_factory,
    *,
    display_name: str | None = None,
    allowed_tools: frozenset[ToolType] = _CUTTERS,
    requires_depth: bool = True,
) -> OperationDefinition:
    return OperationDefinition(
        operation_type=operation_type,
        display_name=display_name or operation_type.value.replace("_", " ").title(),
        parameters_model=parameters_model,
        strategy_factory=strategy_factory,
        family=OperationFamily.MILLING,
        allowed_tool_types=allowed_tools,
        requires_depth=requires_depth,
    )


def _drilling_definition(
    operation_type: OperationType,
    parameters_model: type[StrategyParameters],
    strategy_factory,
    *,
    display_name: str | None = None,
    allowed_tools: frozenset[ToolType],
    capabilities: frozenset[str] = frozenset(),
) -> OperationDefinition:
    return OperationDefinition(
        operation_type=operation_type,
        display_name=display_name or operation_type.value.replace("_", " ").title(),
        parameters_model=parameters_model,
        strategy_factory=strategy_factory,
        family=OperationFamily.DRILLING,
        allowed_tool_types=allowed_tools,
        required_capabilities=capabilities,
    )


def _carving_definition(
    operation_type: OperationType,
    parameters_model: type[StrategyParameters],
    strategy_factory,
    *,
    display_name: str | None = None,
    allowed_tools: frozenset[ToolType] = _CARVERS,
    capabilities: frozenset[str] = frozenset(),
) -> OperationDefinition:
    return OperationDefinition(
        operation_type=operation_type,
        display_name=display_name or operation_type.value.replace("_", " ").title(),
        parameters_model=parameters_model,
        strategy_factory=strategy_factory,
        family=OperationFamily.CARVING,
        allowed_tool_types=allowed_tools,
        required_capabilities=capabilities,
    )


def build_standard_registry() -> OperationRegistry:
    """Build the canonical registry without retaining mutable global state."""
    from antcam_rc2.core.operations.carving import (
        ChamferingStrategy,
        EngravingStrategy,
        FilletingStrategy,
        VCarveRoughingStrategy,
        VCarvingStrategy,
    )
    from antcam_rc2.core.operations.drilling import (
        BoringStrategy,
        DrillStrategy,
        HolePocketingStrategy,
        HolesStrategy,
        TappingStrategy,
        ThreadMillingStrategy,
    )
    from antcam_rc2.core.operations.milling import (
        FaceTopStrategy,
        FacingStrategy,
        PocketStrategy,
        ProfileStrategy,
        RoughingStrategy,
        SlottingStrategy,
        TSlottingStrategy,
    )

    strategy_map = {
        OperationType.FACE_TOP: (FaceTopStrategy, FaceTopParameters, _CUTTERS, frozenset(), False),
        OperationType.ROUGHING: (RoughingStrategy, RoughingParameters, _CUTTERS, frozenset(), True),
        OperationType.FACING: (FacingStrategy, FacingParameters, _CUTTERS, frozenset(), True),
        OperationType.POCKETING: (PocketStrategy, PocketParameters, _CUTTERS, frozenset(), True),
        OperationType.PROFILING: (ProfileStrategy, ProfileParameters, _CUTTERS, frozenset(), True),
        OperationType.SLOTTING: (SlottingStrategy, SlottingParameters, _CUTTERS, frozenset(), True),
        OperationType.T_SLOTTING: (TSlottingStrategy, TSlotParameters, frozenset({ToolType.T_SLOT}), frozenset(), True),
        OperationType.HOLES: (
            HolesStrategy,
            HolesParameters,
            frozenset({ToolType.DRILL, ToolType.BORE}),
            frozenset(),
            True,
        ),
        OperationType.DRILL: (DrillStrategy, DrillParameters, frozenset({ToolType.DRILL}), frozenset(), True),
        OperationType.BORING: (BoringStrategy, BoringParameters, frozenset({ToolType.BORE}), frozenset(), True),
        OperationType.HOLE_POCKETING: (HolePocketingStrategy, HolePocketingParameters, _CUTTERS, frozenset(), True),
        OperationType.THREAD_MILLING: (
            ThreadMillingStrategy,
            ThreadMillingParameters,
            frozenset({ToolType.THREAD_MILL}),
            frozenset(),
            True,
        ),
        OperationType.TAPPING: (
            TappingStrategy,
            TappingParameters,
            frozenset({ToolType.TAP}),
            frozenset({"rigid_tapping"}),
            True,
        ),
        OperationType.V_CARVE_ROUGHING: (
            VCarveRoughingStrategy,
            VCarveRoughingParameters,
            frozenset({ToolType.END_MILL}),
            frozenset(),
            True,
        ),
        OperationType.V_CARVING: (
            VCarvingStrategy,
            VCarvingParameters,
            frozenset({ToolType.V_BIT}),
            frozenset(),
            True,
        ),
        OperationType.ENGRAVING: (EngravingStrategy, EngravingParameters, _CARVERS, frozenset(), True),
        OperationType.CHAMFERING: (
            ChamferingStrategy,
            ChamferingParameters,
            frozenset({ToolType.V_BIT}),
            frozenset(),
            True,
        ),
        OperationType.FILLETTING: (
            FilletingStrategy,
            FilletingParameters,
            frozenset({ToolType.BALL, ToolType.BULL}),
            frozenset({"3d_toolpath"}),
            True,
        ),
    }

    definitions: list[OperationDefinition] = []
    for operation_type, spec in strategy_map.items():
        strategy_cls, parameters_model, allowed_tools, capabilities, requires_depth = spec
        if operation_type in {
            OperationType.HOLES,
            OperationType.DRILL,
            OperationType.BORING,
            OperationType.THREAD_MILLING,
            OperationType.TAPPING,
        }:
            definition = _drilling_definition(
                operation_type,
                parameters_model,
                lambda cls=strategy_cls: cls(),
                allowed_tools=allowed_tools,
                capabilities=capabilities,
            )
        elif operation_type in {
            OperationType.V_CARVE_ROUGHING,
            OperationType.V_CARVING,
            OperationType.ENGRAVING,
            OperationType.CHAMFERING,
            OperationType.FILLETTING,
        }:
            definition = _carving_definition(
                operation_type,
                parameters_model,
                lambda cls=strategy_cls: cls(),
                allowed_tools=allowed_tools,
                capabilities=capabilities,
            )
        else:
            definition = _milling_definition(
                operation_type,
                parameters_model,
                lambda cls=strategy_cls: cls(),
                allowed_tools=allowed_tools,
                requires_depth=requires_depth,
            )
        definitions.append(definition)

    return OperationRegistry(definitions)
