"""End-to-end headless UI workflow: project -> import -> operations -> plan."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def ui(qapp, app_config):
    from antcam_rc2.app.application import Application
    from antcam_rc2.frontends.pyside.app_window import MainWindow
    from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController

    core = Application(config=app_config)
    controller = ProjectController(core)
    window = MainWindow(controller)
    window.show()
    yield controller, window
    window.close()
    core.shutdown()


def pump_until(qapp, predicate, timeout_ms: int = 3000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_full_workflow_creates_imports_plans(ui, qapp, tmp_path: Path) -> None:
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication

    from antcam_rc2.core.project.models import OperationParameters, Stock

    controller, window = ui
    controller.new_project(
        "UI fixture", "makera_z1", Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061")
    )
    assert controller.project is not None

    geometry = Path(__file__).parent.parent / "data" / "mini_polyline_bulge.dxf"
    # Import geometry asynchronously and wait for it to complete
    loop = QEventLoop()
    controller.scene_changed.connect(loop.quit)
    controller.import_geometry(geometry)
    QApplication.processEvents()
    loop.exec()

    assert controller.scene is not None
    assert len(controller.render_scene().nodes) >= 1

    operation_id = controller.add_operation("pocketing", tool_id="end_mill_3_175_2f", cooling_id="aerodust")
    assert operation_id is not None
    controller.select_operation(operation_id)

    # Select geometry (simulates viewport picking) and set a valid depth.
    controller.update_geometry_refs(operation_id, "0", 0)
    controller.update_operation_parameters(operation_id, OperationParameters(depth_mm=1.0, stepover_mm=1.0))

    # Generate toolpath asynchronously and wait for it to complete
    loop = QEventLoop()
    controller.toolpath_controller.plan_ready.connect(loop.quit)
    controller.toolpath_controller.plan_failed.connect(loop.quit)
    controller.generate_toolpath()
    QApplication.processEvents()
    loop.exec()

    assert pump_until(qapp, lambda: controller.last_plan is not None)

    plan = controller.last_plan
    assert plan is not None
    assert plan.is_executable
    assert plan.operations[0].status.value == "succeeded"


def test_operations_actions_via_controller(ui, qapp) -> None:
    from antcam_rc2.core.project.models import Stock

    controller, _window = ui
    controller.new_project(
        "actions", "makera_z1", Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061")
    )
    controller.add_operation("profiling", tool_id="end_mill_3_175_2f", cooling_id="aerodust")
    second = controller.add_operation("drill", tool_id="drill_2", cooling_id="aerodust")
    assert len(controller.project.operations) == 2

    controller.duplicate_operation(second)
    assert len(controller.project.operations) == 3

    controller.toggle_operation(second, False)
    operations = {op.id: op.enabled for op in controller.project.operations}
    assert operations[second] is False

    controller.remove_operation(second)
    assert len(controller.project.operations) == 2

    controller.undo()
    assert len(controller.project.operations) == 3
    controller.redo()
    assert len(controller.project.operations) == 2


def test_geometry_refs_added_through_picking(ui, qapp) -> None:
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication

    from antcam_rc2.core.project.geometry_refs import create_geometry_ref
    from antcam_rc2.core.project.models import Stock

    controller, _window = ui
    controller.new_project(
        "picking", "makera_z1", Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061")
    )
    geometry = Path(__file__).parent.parent / "data" / "mini_polyline_bulge.dxf"
    # Import geometry asynchronously and wait for it to complete
    loop = QEventLoop()
    controller.scene_changed.connect(loop.quit)
    controller.import_geometry(geometry)
    QApplication.processEvents()
    loop.exec()

    operation_id = controller.add_operation("profiling", tool_id="end_mill_3_175_2f", cooling_id="aerodust")
    controller.select_operation(operation_id)

    expected = create_geometry_ref(controller.scene, "0", 0)
    controller.update_geometry_refs(operation_id, "0", 0)
    operation = next(op for op in controller.project.operations if op.id == operation_id)
    assert len(operation.geometry_refs) == 1
    assert operation.geometry_refs[0].entity_fingerprint == expected.entity_fingerprint


def test_ui_undo_redo_through_window_actions(ui, qapp) -> None:
    from antcam_rc2.core.project.models import Stock

    controller, window = ui
    controller.new_project(
        "undo", "makera_z1", Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061")
    )
    controller.add_operation("drill", tool_id="drill_2", cooling_id="aerodust")
    assert len(controller.project.operations) == 1

    controller.undo()
    assert len(controller.project.operations) == 0
    controller.redo()
    assert len(controller.project.operations) == 1
