"""Tests for the core-event -> Qt-signal bridge (offscreen)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def ui(qapp, app_config):
    from antcam_rc2.app.application import Application
    from antcam_rc2.frontends.pyside.controllers.event_bridge import EventBridge

    core = Application(config=app_config)
    bridge = EventBridge(core.event_bus)
    yield core, bridge
    bridge.dispose()
    core.shutdown()


def test_bridge_forwards_domain_events(qapp, ui) -> None:
    from antcam_rc2.core.project.models import Stock

    core, bridge = ui
    received: list[tuple] = []
    bridge.project_created.connect(lambda project_id: received.append(("created", project_id)))
    bridge.operation_added.connect(lambda project_id, operation_id: received.append(("added", operation_id)))

    project = core.project_service.create_project(
        "bridge",
        machine_id="makera_z1",
        stock=Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061"),
    )
    operation = core.project_service.add_operation(project.id, "drill", tool_id="drill_2", cooling_id="aerodust")

    assert ("created", project.id) in received
    assert ("added", operation.id) in received


def test_bridge_forwards_fixture_events(qapp, ui) -> None:
    from antcam_rc2.core.project.models import Fixture, FixtureKind, Stock

    core, bridge = ui
    received: list[tuple] = []
    bridge.fixture_added.connect(lambda project_id, fixture_id: received.append(("added", fixture_id)))
    bridge.fixture_changed.connect(lambda project_id, fixture_id: received.append(("changed", fixture_id)))
    bridge.fixture_removed.connect(lambda project_id, fixture_id: received.append(("removed", fixture_id)))

    project = core.project_service.create_project(
        "bridge fixtures",
        machine_id="makera_z1",
        stock=Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061"),
    )
    fixture = Fixture(
        id="fix_1234abcd", name="Clamp", kind=FixtureKind.FIXED, width_mm=20.0, length_mm=20.0, height_mm=10.0
    )
    core.project_service.add_fixture(project.id, fixture)
    core.project_service.replace_fixture(project.id, fixture.id, fixture.model_copy(update={"name": "Vise"}))
    core.project_service.remove_fixture(project.id, fixture.id)

    assert ("added", fixture.id) in received
    assert ("changed", fixture.id) in received
    assert ("removed", fixture.id) in received


def test_bridge_dispose_stops_delivery(qapp, ui) -> None:
    from antcam_rc2.core.project.models import Stock

    core, bridge = ui
    received: list = []
    bridge.stock_changed.connect(lambda project_id: received.append(project_id))
    bridge.dispose()

    project = core.project_service.create_project(
        "bridge",
        machine_id="makera_z1",
        stock=Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061"),
    )
    core.project_service.replace_stock(
        project.id, Stock(width_mm=40, length_mm=20, height_mm=5, material_id="aluminum_6061")
    )
    assert received == []
