"""Tests for command-backed project lifecycle and setup mutations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from antcam_rc2.app.event_bus import EventBus
from antcam_rc2.app.events import (
    FixtureAdded,
    FixtureChanged,
    FixtureRemoved,
    MachineChanged,
    OperationAdded,
    OperationRemoved,
    OperationReordered,
    StockChanged,
)
from antcam_rc2.core.databases.repository import CatalogRepository
from antcam_rc2.core.errors import ProjectError
from antcam_rc2.core.geometry.curves import LineSegment
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import Fixture, FixtureKind, OperationParameters, OperationType, Stock
from antcam_rc2.core.project.repository import InMemoryProjectRepository
from antcam_rc2.core.services.command_stack import CommandStack
from antcam_rc2.core.services.project_service import ProjectService


def make_service() -> tuple[ProjectService, CommandStack, EventBus]:
    stack = CommandStack()
    events = EventBus()
    service = ProjectService(
        project_repository=InMemoryProjectRepository(),
        catalog_repository=CatalogRepository.from_package(),
        command_stack=stack,
        event_bus=events,
        clock=lambda: datetime.now(UTC),
    )
    return service, stack, events


def make_stock() -> Stock:
    return Stock(width_mm=100.0, length_mm=80.0, height_mm=12.0, material_id="aluminum_6061")


def add_profile(service: ProjectService, project_id: str, name: str = "Outer profile"):
    return service.add_operation(
        project_id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        name=name,
        operation_parameters=OperationParameters(depth_mm=4.0),
    )


def test_create_add_duplicate_reorder_and_undo_redo_use_exact_snapshots() -> None:
    service, stack, _ = make_service()
    project = service.create_project("Fixture plate", machine_id="makera_z1", stock=make_stock())
    first = add_profile(service, project.id, "First")
    second = add_profile(service, project.id, "Second")

    duplicate = service.duplicate_operation(project.id, first.id)
    assert [operation.name for operation in service.list_operations(project.id)] == ["First", "First copy", "Second"]
    assert duplicate.id != first.id

    service.move_operation(project.id, second.id, 0)
    assert [operation.name for operation in service.list_operations(project.id)] == ["Second", "First", "First copy"]
    assert stack.can_undo

    assert service.undo()
    assert [operation.name for operation in service.list_operations(project.id)] == ["First", "First copy", "Second"]
    assert service.redo()
    assert [operation.name for operation in service.list_operations(project.id)] == ["Second", "First", "First copy"]


def test_operation_mutations_emit_events_and_validate_catalog_references() -> None:
    service, _, events = make_service()
    received: list[object] = []
    events.subscribe(OperationAdded, received.append)
    events.subscribe(OperationRemoved, received.append)
    events.subscribe(OperationReordered, received.append)
    project = service.create_project("Fixture plate", machine_id="makera_z1", stock=make_stock())

    operation = add_profile(service, project.id)
    service.move_operation(project.id, operation.id, 0)
    service.remove_operation(project.id, operation.id)

    assert [type(event) for event in received] == [OperationAdded, OperationRemoved]
    with pytest.raises(ProjectError, match="unknown tool"):
        service.add_operation(
            project.id,
            OperationType.PROFILING,
            tool_id="missing_tool",
            cooling_id="aerodust",
        )


def test_stock_fixture_and_enabled_state_are_command_backed() -> None:
    service, _, events = make_service()
    received: list[object] = []
    events.subscribe(StockChanged, received.append)
    events.subscribe(FixtureAdded, received.append)
    events.subscribe(MachineChanged, received.append)
    project = service.create_project("Fixture plate", machine_id="makera_z1", stock=make_stock())
    operation = add_profile(service, project.id)
    fixture = Fixture(
        id="fix_1234abcd",
        name="Clamp",
        kind=FixtureKind.FIXED,
        width_mm=20.0,
        length_mm=20.0,
        height_mm=10.0,
    )

    service.toggle_operation(project.id, operation.id, False)
    service.add_fixture(project.id, fixture)
    service.replace_stock(
        project.id,
        Stock(width_mm=120.0, length_mm=80.0, height_mm=12.0, material_id="aluminum_6061"),
    )
    service.select_machine(project.id, "makera_z1")

    updated = service.get_project(project.id)
    assert not updated.operations[0].enabled
    assert updated.fixtures == (fixture,)
    assert updated.stock.width_mm == 120.0
    assert [type(event) for event in received] == [FixtureAdded, StockChanged]


def test_fixture_replace_and_remove_are_command_backed() -> None:
    service, stack, events = make_service()
    received: list[object] = []
    events.subscribe(FixtureChanged, received.append)
    events.subscribe(FixtureRemoved, received.append)
    project = service.create_project("Fixture edit", machine_id="makera_z1", stock=make_stock())
    fixture = Fixture(
        id="fix_1234abcd", name="Clamp", kind=FixtureKind.FIXED, width_mm=20.0, length_mm=20.0, height_mm=10.0
    )
    service.add_fixture(project.id, fixture)

    edited = fixture.model_copy(update={"name": "Vise", "kind": FixtureKind.VISE, "width_mm": 40.0})
    service.replace_fixture(project.id, fixture.id, edited)
    assert service.get_project(project.id).fixtures[0].name == "Vise"
    assert service.get_project(project.id).fixtures[0].kind is FixtureKind.VISE
    assert isinstance(received[-1], FixtureChanged)

    service.remove_fixture(project.id, fixture.id)
    assert service.get_project(project.id).fixtures == ()
    assert isinstance(received[-1], FixtureRemoved)

    service.undo()
    assert service.get_project(project.id).fixtures == (edited,)
    service.redo()
    assert service.get_project(project.id).fixtures == ()


def test_project_service_exports_imports_and_binds_transient_geometry(tmp_path: Path) -> None:
    service, _, _ = make_service()
    project = service.create_project("Fixture plate", machine_id="makera_z1", stock=make_stock())
    scene = GeometryScene(source=SourceInfo(format="dxf", path="fixture.dxf"))
    scene.add_entity(LineSegment(Point2(0.0, 0.0), Point2(10.0, 0.0)), "profile")

    service.attach_geometry(project.id, scene)
    destination = tmp_path / "fixture.antcam.json"
    service.export_project(project.id, destination)

    imported_service, _, _ = make_service()
    imported = imported_service.import_project(destination)

    assert imported.id == project.id
    assert imported.geometry_binding is not None
    assert imported.geometry_binding.source.path == "fixture.dxf"


def test_project_service_reports_stale_geometry_references_without_retargeting() -> None:
    service, _, _ = make_service()
    project = service.create_project("Fixture plate", machine_id="makera_z1", stock=make_stock())
    scene = GeometryScene(source=SourceInfo(format="dxf", path="fixture.dxf"))
    scene.add_entity(LineSegment(Point2(0.0, 0.0), Point2(10.0, 0.0)), "profile")
    reference = create_geometry_ref(scene, "profile", 0)
    service.attach_geometry(project.id, scene)
    service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(reference,),
    )
    scene.layer("profile", create=False).entities[0] = LineSegment(Point2(0.0, 2.0), Point2(10.0, 2.0))

    errors = service.validate_geometry_references(project.id)

    assert len(errors) == 1
    assert errors[0].code == "geometry_reference_error"
