"""Command-backed application service for persistent project setup state."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from antcam_rc2.app.event_bus import EventBus
from antcam_rc2.core.databases.repository import CatalogRepository
from antcam_rc2.core.errors import CatalogError, GeometryReferenceError, ProjectError
from antcam_rc2.core.identifiers import new_id
from antcam_rc2.core.io.scene import GeometryScene
from antcam_rc2.core.project.geometry_refs import (
    bind_geometry,
    invalidate_scene_index_cache,
    resolve_geometry_ref,
)
from antcam_rc2.core.project.models import (
    Fixture,
    GeometryRef,
    Operation,
    OperationParameters,
    OperationType,
    Project,
    SolidPlacement,
    SolidRef,
    Stock,
)
from antcam_rc2.core.project.persistence import (
    ProjectDocument,
    read_project_document,
    write_project_document,
)
from antcam_rc2.core.project.repository import ProjectRepository
from antcam_rc2.core.project.solid_refs import bind_solid

if False:  # TYPE_CHECKING
    from antcam_rc2.core.geometry3d.scene import SolidScene  # noqa: F401
from antcam_rc2.core.services.command_stack import CommandStack
from antcam_rc2.core.services.events import (
    Event,
    FixtureAdded,
    FixtureChanged,
    FixtureRemoved,
    MachineChanged,
    OperationAdded,
    OperationRemoved,
    OperationReordered,
    OperationToggled,
    ProjectCreated,
    ProjectLoaded,
    StockChanged,
)
from antcam_rc2.core.services.project_commands import ReplaceProjectCommand

_LOGGER = logging.getLogger(__name__)


class ProjectService:
    """Manage immutable project snapshots through application-level commands."""

    def __init__(
        self,
        *,
        project_repository: ProjectRepository,
        catalog_repository: CatalogRepository,
        command_stack: CommandStack,
        event_bus: EventBus,
        clock: Callable[[], datetime],
    ) -> None:
        self._projects = project_repository
        self._catalogs = catalog_repository
        self._command_stack = command_stack
        self._event_bus = event_bus
        self._clock = clock
        self._scenes: dict[str, GeometryScene] = {}
        self._solid_scenes: dict[str, object] = {}  # project_id -> SolidScene (object to avoid cycle)

    def create_project(self, name: str, *, machine_id: str, stock: Stock) -> Project:
        """Create and store a catalog-valid project setup."""
        self._validate_machine(machine_id)
        self._validate_material(stock.material_id)
        now = self._now()
        project = Project(
            id=new_id("proj"),
            name=name,
            created_at=now,
            modified_at=now,
            machine_id=machine_id,
            stock=stock,
        )
        self._projects.add(project)
        self._emit(ProjectCreated(project_id=project.id))
        return project

    def get_project(self, project_id: str) -> Project:
        """Return the current immutable snapshot for ``project_id``."""
        return self._projects.get(project_id)

    def list_operations(self, project_id: str) -> list[Operation]:
        """Return all operations in durable execution order, including disabled ones."""
        return list(self.get_project(project_id).operations)

    def add_operation(
        self,
        project_id: str,
        operation_type: OperationType | str,
        *,
        tool_id: str,
        cooling_id: str,
        name: str | None = None,
        geometry_refs: tuple[GeometryRef, ...] = (),
        solid_refs: tuple[SolidRef, ...] = (),
        operation_parameters: OperationParameters | None = None,
    ) -> Operation:
        """Create and append a catalog-valid operation through one command."""
        project = self.get_project(project_id)
        resolved_type = OperationType(operation_type)
        self._validate_operation_catalogs(project, tool_id, cooling_id)
        operation = Operation(
            id=new_id("op"),
            name=name or f"{resolved_type.value}_{len(project.operations) + 1}",
            operation_type=resolved_type,
            tool_id=tool_id,
            cooling_id=cooling_id,
            geometry_refs=geometry_refs,
            solid_refs=solid_refs,
            parameters=operation_parameters or OperationParameters(),
        )
        after = self._updated(project, operations=project.operations + (operation,))
        self._commit(project, after, f"Add operation '{operation.name}'")
        self._emit(
            OperationAdded(
                project_id=project.id, operation_id=operation.id, operation_type=operation.operation_type.value
            )
        )
        return operation

    def remove_operation(self, project_id: str, operation_id: str) -> None:
        """Remove an operation from a project."""
        project = self.get_project(project_id)
        index = self._operation_index(project, operation_id)
        after = self._updated(project, operations=project.operations[:index] + project.operations[index + 1 :])
        self._commit(project, after, f"Remove operation '{operation_id}'")
        self._emit(OperationRemoved(project_id=project.id, operation_id=operation_id))

    def move_operation(self, project_id: str, operation_id: str, new_index: int) -> None:
        """Reorder an operation within a project."""
        project = self.get_project(project_id)
        old_index = self._operation_index(project, operation_id)
        if not 0 <= new_index < len(project.operations):
            raise ProjectError(f"operation index out of range: {new_index}")
        if old_index == new_index:
            return
        operations = list(project.operations)
        operation = operations.pop(old_index)
        operations.insert(new_index, operation)
        after = self._updated(project, operations=tuple(operations))
        self._commit(project, after, f"Move operation '{operation_id}'")
        self._emit(OperationReordered(project_id=project.id, operation_id=operation_id, new_index=new_index))

    def toggle_operation(self, project_id: str, operation_id: str, enabled: bool) -> None:
        """Enable or disable an operation."""
        project = self.get_project(project_id)
        index = self._operation_index(project, operation_id)
        operation = project.operations[index]
        if operation.enabled == enabled:
            return
        replacement = operation.model_copy(update={"enabled": enabled})
        operations = project.operations[:index] + (replacement,) + project.operations[index + 1 :]
        self._commit(project, self._updated(project, operations=operations), f"Toggle operation '{operation_id}'")
        self._emit(OperationToggled(project_id=project.id, operation_id=operation_id, enabled=enabled))

    def duplicate_operation(self, project_id: str, operation_id: str) -> Operation:
        """Clone an operation immediately after its source operation."""
        project = self.get_project(project_id)
        index = self._operation_index(project, operation_id)
        source = project.operations[index]
        duplicate = source.model_copy(update={"id": new_id("op"), "name": f"{source.name} copy"})
        operations = project.operations[: index + 1] + (duplicate,) + project.operations[index + 1 :]
        self._commit(project, self._updated(project, operations=operations), f"Duplicate operation '{operation_id}'")
        self._emit(
            OperationAdded(
                project_id=project.id,
                operation_id=duplicate.id,
                operation_type=duplicate.operation_type.value,
            )
        )
        return duplicate

    def replace_geometry_refs(self, project_id: str, operation_id: str, geometry_refs: tuple[GeometryRef, ...]) -> None:
        """Replace the geometry selection of one operation (viewport picking).

        The refs must already be validated against the attached scene; the
        command keeps the change undoable.
        """
        project = self.get_project(project_id)
        index = self._operation_index(project, operation_id)
        operation = project.operations[index]
        replacement = operation.model_copy(update={"geometry_refs": geometry_refs})
        operations = project.operations[:index] + (replacement,) + project.operations[index + 1 :]
        self._commit(project, self._updated(project, operations=operations), f"Set geometry refs on '{operation_id}'")
        self._emit(OperationToggled(project_id=project.id, operation_id=operation_id, enabled=operation.enabled))

    def replace_solid_refs(self, project_id: str, operation_id: str, solid_refs: tuple[SolidRef, ...]) -> None:
        """Replace the 3D feature selection of one operation (undoable)."""
        project = self.get_project(project_id)
        index = self._operation_index(project, operation_id)
        operation = project.operations[index]
        replacement = operation.model_copy(update={"solid_refs": solid_refs})
        operations = project.operations[:index] + (replacement,) + project.operations[index + 1 :]
        self._commit(project, self._updated(project, operations=operations), f"Set solid refs on '{operation_id}'")
        self._emit(OperationToggled(project_id=project.id, operation_id=operation_id, enabled=operation.enabled))

    def replace_operation_parameters(self, project_id: str, operation_id: str, parameters: OperationParameters) -> None:
        """Replace the parameters of one operation through one undoable command."""
        project = self.get_project(project_id)
        index = self._operation_index(project, operation_id)
        operation = project.operations[index]
        replacement = operation.model_copy(update={"parameters": parameters})
        operations = project.operations[:index] + (replacement,) + project.operations[index + 1 :]
        self._commit(project, self._updated(project, operations=operations), f"Update parameters on '{operation_id}'")
        self._emit(OperationToggled(project_id=project.id, operation_id=operation_id, enabled=operation.enabled))

    def replace_stock(self, project_id: str, stock: Stock) -> None:
        """Replace stock after validating its material catalog binding."""
        project = self.get_project(project_id)
        self._validate_material(stock.material_id)
        self._commit(project, self._updated(project, stock=stock), "Replace stock")
        self._emit(StockChanged(project_id=project.id))

    def add_fixture(self, project_id: str, fixture: Fixture) -> None:
        """Append one fixture through a command-backed project replacement."""
        project = self.get_project(project_id)
        self._commit(
            project, self._updated(project, fixtures=project.fixtures + (fixture,)), f"Add fixture '{fixture.name}'"
        )
        self._emit(FixtureAdded(project_id=project.id, fixture_id=fixture.id))

    def remove_fixture(self, project_id: str, fixture_id: str) -> None:
        """Remove one fixture by ID."""
        project = self.get_project(project_id)
        fixtures = tuple(fixture for fixture in project.fixtures if fixture.id != fixture_id)
        if len(fixtures) == len(project.fixtures):
            raise ProjectError(f"fixture not found: {fixture_id}")
        self._commit(project, self._updated(project, fixtures=fixtures), f"Remove fixture '{fixture_id}'")
        self._emit(FixtureRemoved(project_id=project.id, fixture_id=fixture_id))

    def replace_fixture(self, project_id: str, fixture_id: str, fixture: Fixture) -> None:
        """Replace one fixture's properties through one undoable command."""
        project = self.get_project(project_id)
        if fixture.id != fixture_id:
            raise ProjectError("fixture id must match the replaced fixture")
        index = self._fixture_index(project, fixture_id)
        fixtures = project.fixtures[:index] + (fixture,) + project.fixtures[index + 1 :]
        self._commit(project, self._updated(project, fixtures=fixtures), f"Edit fixture '{fixture.name}'")
        self._emit(FixtureChanged(project_id=project.id, fixture_id=fixture_id))

    def select_machine(self, project_id: str, machine_id: str) -> None:
        """Select another known machine profile for the project."""
        project = self.get_project(project_id)
        self._validate_machine(machine_id)
        if project.machine_id == machine_id:
            return
        self._commit(project, self._updated(project, machine_id=machine_id), f"Select machine '{machine_id}'")
        self._emit(MachineChanged(project_id=project.id, machine_id=machine_id))

    def attach_geometry(self, project_id: str, scene: GeometryScene) -> None:
        """Attach a transient scene and persist only its source/fingerprint binding.

        The index cache is invalidated explicitly: a re-attached scene (possibly
        a new instance or a mutated one) must always be rebuilt.
        """
        invalidate_scene_index_cache()
        project = self.get_project(project_id)
        self._scenes[project_id] = scene
        self._commit(project, self._updated(project, geometry_binding=bind_geometry(scene)), "Attach geometry")

    def clear_solid_refs(self, project_id: str) -> None:
        """Remove every ``solid_ref`` from all operations (undoable)."""
        project = self.get_project(project_id)
        if not any(op.solid_refs for op in project.operations):
            return
        operations = tuple(op.model_copy(update={"solid_refs": ()}) for op in project.operations)
        self._commit(project, self._updated(project, operations=operations), "Clear solid refs")

    def attach_solid(self, project_id: str, scene: object, placement: SolidPlacement | None = None) -> None:
        """Attach a transient 3D solid scene and persist binding + placement.

        ``placement`` defaults to the current project placement (kept) or None.
        Any ``solid_refs`` from a previous solid are cleared atomically when
        the binding changes so stale references never survive a replacement.
        """
        from antcam_rc2.core.geometry3d.scene import SolidScene  # noqa: WPS433 - lazy to avoid cycle

        if not isinstance(scene, SolidScene):
            raise ProjectError(f"attach_solid expects SolidScene, got {type(scene).__name__}")
        project = self.get_project(project_id)
        binding = bind_solid(scene)
        # Keep existing placement if caller did not supply a new one.
        new_placement = placement if placement is not None else project.solid_placement
        # If binding changed, stale solid_refs must be cleared in the same commit.
        needs_clear = project.solid_binding is not None and project.solid_binding != binding
        if needs_clear and any(op.solid_refs for op in project.operations):
            operations: tuple[Operation, ...] = tuple(
                op.model_copy(update={"solid_refs": ()}) for op in project.operations
            )
            after = self._updated(
                project, solid_binding=binding, solid_placement=new_placement, operations=operations
            )
        else:
            after = self._updated(project, solid_binding=binding, solid_placement=new_placement)
        self._solid_scenes[project_id] = scene
        self._commit(project, after, "Attach solid")

    def replace_solid_placement(self, project_id: str, placement: SolidPlacement | None) -> None:
        """Replace the non-destructive solid placement (undoable)."""
        project = self.get_project(project_id)
        self._commit(project, self._updated(project, solid_placement=placement), "Set solid placement")

    def detach_solid(self, project_id: str) -> None:
        """Remove the transient solid scene and its persisted binding/placement (undoable).

        All ``solid_refs`` on operations are cleared in the same commit so the
        project never retains stale 3D references after the solid is gone.
        """
        project = self.get_project(project_id)
        if project.solid_binding is None and project.solid_placement is None and project_id not in self._solid_scenes:
            return
        self._solid_scenes.pop(project_id, None)
        if any(op.solid_refs for op in project.operations):
            operations: tuple[Operation, ...] = tuple(
                op.model_copy(update={"solid_refs": ()}) for op in project.operations
            )
            after = self._updated(project, solid_binding=None, solid_placement=None, operations=operations)
        else:
            after = self._updated(project, solid_binding=None, solid_placement=None)
        self._commit(project, after, "Remove solid")

    def get_solid_scene(self, project_id: str) -> object | None:
        """Return the attached transient SolidScene for ``project_id``, if any."""
        return self._solid_scenes.get(project_id)

    def validate_geometry_references(self, project_id: str) -> tuple[GeometryReferenceError, ...]:
        """Return safe-reference errors for the currently attached scene, if any."""
        project = self.get_project(project_id)
        scene = self._scenes.get(project_id)
        if scene is None:
            return ()
        errors: list[GeometryReferenceError] = []
        for operation in project.operations:
            for reference in operation.geometry_refs:
                try:
                    resolve_geometry_ref(reference, scene)
                except GeometryReferenceError as exc:
                    errors.append(exc)
        return tuple(errors)

    def export_project(self, project_id: str, destination: Path) -> None:
        """Write a versioned project document with current catalog versions."""
        project = self.get_project(project_id)
        document = ProjectDocument(
            project=project,
            catalog_snapshot={"versions": self._catalogs.catalog_versions()},
        )
        write_project_document(destination, document)

    def import_project(self, source: Path) -> Project:
        """Load a document only after validating all current catalog references."""
        document = read_project_document(source)
        self._validate_project_catalogs(document.project)
        self._projects.add(document.project)
        self._report_catalog_version_mismatch(document)
        self._emit(ProjectLoaded(project_id=document.project.id, name=document.project.name))
        return document.project

    def undo(self) -> bool:
        """Undo the latest successful mutable setup command."""
        return self._command_stack.undo()

    def redo(self) -> bool:
        """Redo the latest undone mutable setup command."""
        return self._command_stack.redo()

    def _commit(self, before: Project, after: Project, label: str) -> None:
        self._command_stack.push(ReplaceProjectCommand(self._projects, before, after, label))

    def _updated(self, project: Project, **changes: object) -> Project:
        values = project.model_dump()
        values.update(changes)
        values["modified_at"] = self._now()
        return Project.model_validate(values)

    def _operation_index(self, project: Project, operation_id: str) -> int:
        for index, operation in enumerate(project.operations):
            if operation.id == operation_id:
                return index
        raise ProjectError(f"operation not found: {operation_id}")

    def _fixture_index(self, project: Project, fixture_id: str) -> int:
        for index, fixture in enumerate(project.fixtures):
            if fixture.id == fixture_id:
                return index
        raise ProjectError(f"fixture not found: {fixture_id}")

    def _validate_machine(self, machine_id: str) -> None:
        try:
            self._catalogs.machine(machine_id)
        except CatalogError as exc:
            raise ProjectError(str(exc)) from exc

    def _validate_material(self, material_id: str) -> None:
        try:
            self._catalogs.material(material_id)
        except CatalogError as exc:
            raise ProjectError(str(exc)) from exc

    def _validate_operation_catalogs(self, project: Project, tool_id: str, cooling_id: str) -> None:
        try:
            self._catalogs.tool(tool_id)
            machine = self._catalogs.machine(project.machine_id)
            self._catalogs.cooling(cooling_id)
        except CatalogError as exc:
            raise ProjectError(str(exc)) from exc
        if cooling_id not in machine.supported_cooling_ids:
            raise ProjectError(f"cooling {cooling_id!r} is not supported by machine {machine.id!r}")

    def _validate_project_catalogs(self, project: Project) -> None:
        self._validate_machine(project.machine_id)
        self._validate_material(project.stock.material_id)
        for operation in project.operations:
            self._validate_operation_catalogs(project, operation.tool_id, operation.cooling_id)

    def _report_catalog_version_mismatch(self, document: ProjectDocument) -> None:
        current = self._catalogs.catalog_versions()
        mismatches = {
            name: (document.catalog_snapshot.versions[name], version)
            for name, version in current.items()
            if document.catalog_snapshot.versions[name] != version
        }
        if mismatches:
            _LOGGER.warning("Catalog versions differ from project snapshot: %s", mismatches)

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)

    def _emit(self, event: Event) -> None:
        try:
            self._event_bus.emit(event)
        except Exception:  # noqa: BLE001 - observers cannot roll back committed state
            _LOGGER.exception("Project event observer failed after a committed change")
