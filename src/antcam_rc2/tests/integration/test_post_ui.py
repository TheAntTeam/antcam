"""Offscreen UI tests for the post-processing export in the toolpath panel."""

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


def pump_until(qapp, predicate, timeout_ms: int = 5000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _prepare(controller) -> None:
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication

    from antcam_rc2.core.project.models import OperationParameters, Stock

    controller.new_project(
        "post ui", "makera_z1", Stock(width_mm=30, length_mm=30, height_mm=10, material_id="aluminum_6061")
    )
    # Import geometry asynchronously and wait for it to complete
    loop = QEventLoop()
    controller.scene_changed.connect(loop.quit)
    controller.import_geometry(Path(__file__).parent.parent / "data" / "mini_polyline_bulge.dxf")
    QApplication.processEvents()
    loop.exec()

    operation_id = controller.add_operation("profiling", tool_id="end_mill_3_175_2f", cooling_id="aerodust")
    controller.update_geometry_refs(operation_id, "0", 0)
    controller.update_operation_parameters(operation_id, OperationParameters(depth_mm=2.0, stepdown_mm=1.0))

    # Generate toolpath asynchronously and wait for it to complete
    loop = QEventLoop()
    controller.toolpath_controller.plan_ready.connect(loop.quit)
    controller.toolpath_controller.plan_failed.connect(loop.quit)
    controller.generate_toolpath()
    QApplication.processEvents()
    loop.exec()


def test_post_combo_lists_registered_posts(qapp, ui) -> None:
    _controller, window = ui
    combo = window._toolpath_panel._post_combo
    assert combo.count() == 3
    assert {combo.itemText(i) for i in range(combo.count())} == {"grbl", "linuxcnc", "makera"}


def test_export_nc_writes_file_and_preview(qapp, ui, tmp_path: Path) -> None:
    controller, window = ui
    _prepare(controller)
    assert pump_until(qapp, lambda: controller.last_plan is not None)
    assert controller.last_plan.is_executable

    panel = window._toolpath_panel
    assert panel._export_nc_button.isEnabled()
    panel._post_combo.setCurrentText("grbl")

    destination = tmp_path / "out.nc"
    from PySide6.QtWidgets import QFileDialog

    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(destination), "G-code (*.nc)"))
    panel._on_export_nc()

    assert destination.exists()
    content = destination.read_text(encoding="utf-8")
    assert content.startswith("; AntCAM RC2 — post grbl")
    assert "M2" in content
    # Preview stats are updated.
    assert "post grbl" in panel._preview._stats.text()


def test_export_nc_disabled_without_executable_plan(qapp, ui) -> None:
    from antcam_rc2.core.project.models import Stock

    controller, window = ui
    controller.new_project(
        "empty", "makera_z1", Stock(width_mm=30, length_mm=30, height_mm=10, material_id="aluminum_6061")
    )
    window._toolpath_panel.refresh()
    assert not window._toolpath_panel._export_nc_button.isEnabled()
