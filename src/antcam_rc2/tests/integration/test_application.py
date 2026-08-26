"""Integration tests for the application bootstrap."""

from __future__ import annotations

from antcam_rc2.app.application import Application
from antcam_rc2.app.event_bus import EventBus
from antcam_rc2.app.events import LogEvent, OperationAdded
from antcam_rc2.core.config import AppConfig
from antcam_rc2.core.project.models import Stock
from antcam_rc2.core.services.command_stack import CommandStack
from antcam_rc2.core.services.project_service import ProjectService


def test_bootstrap_registers_services(app_config: AppConfig) -> None:
    core = Application(config=app_config)
    try:
        assert isinstance(core.event_bus, EventBus)
        assert isinstance(core.command_stack, CommandStack)
        assert isinstance(core.project_service, ProjectService)
    finally:
        core.shutdown()


def test_application_resolves_services_through_container(app_config: AppConfig) -> None:
    core = Application(config=app_config)
    try:
        resolved_bus = core.services.resolve("event_bus")
        assert resolved_bus is core.event_bus
    finally:
        core.shutdown()


def test_event_bus_circuit(application: Application) -> None:
    received: list[LogEvent] = []
    application.event_bus.subscribe(LogEvent, received.append)
    application.event_bus.emit(LogEvent(level="INFO", message="hello", source="test"))
    assert len(received) == 1


def test_operation_added_event_serializes(application: Application) -> None:
    event = OperationAdded(project_id="p_1", operation_id="op_1", operation_type="profiling")
    assert event.model_dump() == {
        "project_id": "p_1",
        "operation_id": "op_1",
        "operation_type": "profiling",
    }


def test_shutdown_idempotent(app_config: AppConfig) -> None:
    core = Application(config=app_config)
    core.shutdown()
    core.shutdown()  # must not raise


def test_project_service_is_wired_with_application_dependencies(application: Application) -> None:
    project = application.project_service.create_project(
        "Fixture plate",
        machine_id="makera_z1",
        stock=Stock(width_mm=100.0, length_mm=80.0, height_mm=12.0, material_id="aluminum_6061"),
    )

    assert application.project_service.get_project(project.id) == project
