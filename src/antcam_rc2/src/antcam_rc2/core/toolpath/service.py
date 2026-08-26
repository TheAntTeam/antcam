"""Headless project-to-toolpath orchestration with fail-closed diagnostics."""

from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from antcam_rc2.core.databases.repository import CatalogRepository
from antcam_rc2.core.errors import AntcamError, GeometryReferenceError, ToolpathError
from antcam_rc2.core.feeds_speeds.calculator import FeedsSpeedsCalculator
from antcam_rc2.core.feeds_speeds.models import FeedSpeedRequest
from antcam_rc2.core.geometry3d.convert import feature_to_entities
from antcam_rc2.core.geometry3d.scene import SolidScene
from antcam_rc2.core.io.scene import GeometryScene
from antcam_rc2.core.operations.contracts import PlanningContext
from antcam_rc2.core.operations.registry import OperationRegistry
from antcam_rc2.core.project.geometry_refs import (
    SceneFingerprintIndex,
    build_scene_index,
    resolve_geometry_ref,
)
from antcam_rc2.core.project.models import Operation, Project
from antcam_rc2.core.project.solid_refs import resolve_solid_ref
from antcam_rc2.core.services.project_service import ProjectService
from antcam_rc2.core.toolpath.diagnostics import ToolpathCode
from antcam_rc2.core.toolpath.models import (
    OperationPlanStatus,
    ToolpathArtifact,
    ToolpathDiagnostic,
    ToolpathOperationResult,
    ToolpathPlan,
    ToolpathSeverity,
)
from antcam_rc2.core.toolpath.settings import PlanningSettings
from antcam_rc2.core.toolpath.setup_frame import stock_bottom_z_mm, stock_top_z_mm

_CAPABILITY_FIELDS = {"rigid_tapping": "rigid_tapping", "3d_toolpath": "three_d_toolpath"}


class ToolpathService:
    """Generate neutral plans without changing project state or catalog data."""

    def __init__(
        self,
        *,
        project_service: ProjectService,
        catalog_repository: CatalogRepository,
        registry: OperationRegistry,
        feeds_speeds: FeedsSpeedsCalculator,
    ) -> None:
        self._projects = project_service
        self._catalogs = catalog_repository
        self._registry = registry
        self._feeds_speeds = feeds_speeds

    def plan_project(self, project_id: str, scene: GeometryScene, settings: PlanningSettings) -> ToolpathPlan:
        """Plan enabled operations in persisted project order without reordering them."""
        return self.plan_snapshot(self._projects.get_project(project_id), scene, settings)

    def plan_snapshot(
        self,
        project: Project,
        scene: GeometryScene,
        settings: PlanningSettings,
        *,
        solid_scene: SolidScene | None = None,
    ) -> ToolpathPlan:
        """Plan from explicit immutable inputs, without touching project state.

        This is the thread-safe entry point for background planning: the caller
        captures ``project`` (an immutable snapshot) and the transient scene on
        the main thread, and this method only reads them.  ``solid_scene`` is
        optional and enables operations that reference 3D features (Fase 9).
        """
        index = build_scene_index(scene)
        binding_error = self._binding_error(project, index)
        results = tuple(
            self._plan_operation(project, operation, scene, index, settings, binding_error, solid_scene)
            for operation in project.operations
        )
        return ToolpathPlan(
            project_id=project.id,
            operations=results,
            catalog_versions=self._catalogs.catalog_versions(),
            registry_versions=self._registry.definition_versions(),
            settings=settings,
        )

    def plan_operation(
        self,
        project_id: str,
        operation_id: str,
        scene: GeometryScene,
        settings: PlanningSettings,
        *,
        solid_scene: SolidScene | None = None,
    ) -> ToolpathOperationResult:
        """Plan exactly one operation, preserving the same safety checks as project planning."""
        project = self._projects.get_project(project_id)
        operation = next((candidate for candidate in project.operations if candidate.id == operation_id), None)
        if operation is None:
            raise ToolpathError(f"operation not found: {operation_id}")
        index = build_scene_index(scene)
        return self._plan_operation(
            project, operation, scene, index, settings, self._binding_error(project, index), solid_scene
        )

    def validate_project_plan_inputs(
        self, project_id: str, scene: GeometryScene, settings: PlanningSettings
    ) -> tuple[ToolpathDiagnostic, ...]:
        """Return every diagnostic (warnings and errors) without failing early."""
        plan = self.plan_project(project_id, scene, settings)
        return tuple(diagnostic for result in plan.operations for diagnostic in result.diagnostics) + plan.diagnostics

    def export_artifact(self, project_id: str, scene: GeometryScene, settings: PlanningSettings) -> ToolpathArtifact:
        """Build the versioned ``*.toolpath.json`` export envelope (Phase 4.11)."""
        project = self._projects.get_project(project_id)
        index = build_scene_index(scene)
        if (
            project.geometry_binding is not None
            and project.geometry_binding.scene_fingerprint != index.scene_fingerprint
        ):
            raise ToolpathError(
                "cannot export an artifact for a scene that does not match the project binding",
                code=ToolpathCode.GEOMETRY_BINDING_MISMATCH.value,
            )
        plan = self.plan_project(project_id, scene, settings)
        return ToolpathArtifact(
            project_id=project.id,
            scene_fingerprint=index.scene_fingerprint,
            project_fingerprint=_project_fingerprint(project),
            plan=plan,
        )

    def _plan_operation(
        self,
        project: Project,
        operation: Operation,
        scene: GeometryScene,
        index: SceneFingerprintIndex,
        settings: PlanningSettings,
        binding_error: str | None,
        solid_scene: SolidScene | None,
    ) -> ToolpathOperationResult:
        if not operation.enabled:
            return ToolpathOperationResult(
                operation_id=operation.id,
                operation_type=operation.operation_type,
                status=OperationPlanStatus.SKIPPED_DISABLED,
            )
        if binding_error is not None:
            return self._failed(operation, ToolpathCode.GEOMETRY_BINDING_MISMATCH, binding_error)
        try:
            definition = self._registry.definition(operation.operation_type)
            params = definition.parameters_model.model_validate(operation.parameters.strategy_parameters)
            entities = tuple(resolve_geometry_ref(reference, scene, index) for reference in operation.geometry_refs)
            solid_features: tuple = ()
            if operation.solid_refs:
                if solid_scene is None:
                    raise GeometryReferenceError("operation references 3D features but no solid scene was provided")
                solid_features = tuple(resolve_solid_ref(reference, solid_scene) for reference in operation.solid_refs)
                entities = entities + tuple(
                    entity for feature in solid_features for entity in feature_to_entities(feature)
                )
            if not entities:
                raise GeometryReferenceError("operation has no selected geometry")
            machine = self._catalogs.machine(project.machine_id)
            tool = self._catalogs.tool(operation.tool_id)
            material = self._catalogs.material(project.stock.material_id)
            cooling = self._catalogs.cooling(operation.cooling_id)
            self._check_tool_compatibility(definition, tool)
            self._check_machine_capabilities(definition, machine)
            feeds = self._feeds_speeds.calculate(
                FeedSpeedRequest(
                    tool=tool,
                    material=material,
                    machine=machine,
                    cooling=cooling,
                    operation_family=definition.family,
                    pitch_mm=getattr(params, "pitch_mm", None),
                    overrides=operation.parameters.feed_speed_overrides,
                )
            )
            top_z_mm = stock_top_z_mm(project.stock, project.wcs)
            if solid_features:
                top_z_mm = solid_features[0].plane_z_mm
            bottom_z_mm = stock_bottom_z_mm(project.stock, project.wcs)
            depths = self._depth_passes(operation, definition, feeds, top_z_mm, bottom_z_mm)
            context = PlanningContext(
                operation=operation,
                params=params,
                entities=entities,
                tool=tool,
                machine=machine,
                feeds_speeds=feeds,
                depth_passes_z_mm=depths,
                top_z_mm=top_z_mm,
                clearance_z_mm=top_z_mm + settings.clearance_z_mm,
                tolerance_mm=scene.tolerance_mm,
            )
            strategy_result = definition.strategy_factory().generate(context)
            return ToolpathOperationResult(
                operation_id=operation.id,
                operation_type=operation.operation_type,
                status=OperationPlanStatus.SUCCEEDED,
                program=strategy_result.program,
                pass_count=len(depths),
                feeds_speeds=feeds,
                diagnostics=strategy_result.diagnostics,
            )
        except (AntcamError, ValueError) as exc:
            return self._failed(operation, _diagnostic_code(exc), str(exc))

    @staticmethod
    def _check_tool_compatibility(definition, tool) -> None:
        if definition.allowed_tool_types and tool.tool_type not in definition.allowed_tool_types:
            allowed = ", ".join(sorted(tool_type.value for tool_type in definition.allowed_tool_types))
            raise ToolpathError(
                f"tool '{tool.id}' ({tool.tool_type.value}) is not compatible with {definition.operation_type.value}; "
                f"allowed: {allowed}",
                code=ToolpathCode.TOOL_INCOMPATIBLE.value,
            )

    @staticmethod
    def _check_machine_capabilities(definition, machine) -> None:
        for capability in sorted(definition.required_capabilities):
            field = _CAPABILITY_FIELDS.get(capability)
            if field is None:
                raise ToolpathError(
                    f"definition {definition.operation_type.value} requires unknown capability '{capability}'",
                    code=ToolpathCode.OPERATION_ERROR.value,
                )
            if not getattr(machine, field):
                code = (
                    ToolpathCode.MACHINE_SPINDLE_SYNC_REQUIRED.value
                    if capability == "rigid_tapping"
                    else ToolpathCode.MACHINE_CAPABILITY_MISSING.value
                )
                raise ToolpathError(
                    f"machine '{machine.id}' does not declare capability '{capability}'",
                    code=code,
                )

    @staticmethod
    def _depth_passes(
        operation: Operation, definition, feeds, top_z_mm: float, bottom_z_mm: float
    ) -> tuple[float, ...]:
        from antcam_rc2.core.toolpath.depth_passes import plan_depth_passes

        depth_mm = operation.parameters.depth_mm
        if definition.requires_depth and depth_mm is None:
            raise ToolpathError(
                f"{definition.operation_type.value} requires depth_mm", code=ToolpathCode.OPERATION_DEPTH_REQUIRED.value
            )
        if depth_mm is None:
            target_z_mm = top_z_mm - operation.parameters.stock_allowance_mm
        else:
            target_z_mm = top_z_mm - depth_mm
        if target_z_mm >= top_z_mm - 1e-9:
            raise ToolpathError(
                f"{definition.operation_type.value} has no material to remove: "
                "set depth_mm or a positive stock_allowance_mm",
                code=ToolpathCode.OPERATION_PARAM_INVALID.value,
            )
        if target_z_mm < bottom_z_mm - 1e-9:
            raise ToolpathError(
                f"operation depth exceeds the stock bottom (target {target_z_mm:.3f} < stock bottom {bottom_z_mm:.3f})",
                code=ToolpathCode.STOCK_DEPTH_EXCEEDED.value,
            )
        max_stepdown = operation.parameters.stepdown_mm or feeds.stepdown_mm
        if max_stepdown is None or max_stepdown <= 0:
            raise ToolpathError("max_stepdown_mm must be positive", code=ToolpathCode.STEPDOWN_ZERO.value)
        return plan_depth_passes(
            top_z_mm=top_z_mm,
            target_z_mm=target_z_mm,
            max_stepdown_mm=max_stepdown,
            stock_bottom_z_mm=bottom_z_mm,
        )

    @staticmethod
    def _binding_error(project: Project, index: SceneFingerprintIndex) -> str | None:
        if project.geometry_binding is None:
            return None
        if project.geometry_binding.scene_fingerprint != index.scene_fingerprint:
            return "attached scene fingerprint does not match the project geometry binding"
        return None

    @staticmethod
    def _failed(operation: Operation, code: ToolpathCode | str, message: str) -> ToolpathOperationResult:
        return ToolpathOperationResult(
            operation_id=operation.id,
            operation_type=operation.operation_type,
            status=OperationPlanStatus.FAILED,
            diagnostics=(
                ToolpathDiagnostic(
                    code=code if isinstance(code, str) else code.value,
                    message=message,
                    severity=ToolpathSeverity.ERROR,
                    operation_id=operation.id,
                ),
            ),
        )


def _diagnostic_code(error: Exception) -> str:
    if isinstance(error, AntcamError):
        return error.code
    if isinstance(error, ValidationError):
        return ToolpathCode.OPERATION_PARAM_INVALID.value
    return ToolpathCode.PLANNING_VALIDATION_ERROR.value


def _project_fingerprint(project: Project) -> str:
    payload = project.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
