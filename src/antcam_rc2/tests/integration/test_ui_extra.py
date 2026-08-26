"""Additional UI tests: setup actions, controller errors, theme and lights."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def ui(qapp, app_config):
    from antcam_rc2.app.application import Application
    from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController

    core = Application(config=app_config)
    controller = ProjectController(core)
    yield core, controller
    core.shutdown()


def pump_until(qapp, predicate, timeout_ms: int = 3000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_setup_actions_update_project(ui) -> None:
    from antcam_rc2.core.project.models import Stock

    _core, controller = ui
    controller.new_project(
        "setup", "makera_z1", Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061")
    )
    controller.replace_stock(Stock(width_mm=50, length_mm=40, height_mm=8, material_id="plastic"))
    assert controller.project.stock.width_mm == 50.0
    assert controller.project.stock.material_id == "plastic"

    controller.select_machine("makera_z1")
    assert controller.project.machine_id == "makera_z1"

    from antcam_rc2.core.identifiers import new_id
    from antcam_rc2.core.project.models import Fixture

    fixture = Fixture(id=new_id("fix"), name="Vise", width_mm=40.0, length_mm=30.0, height_mm=10.0)
    controller.add_fixture(fixture)
    assert len(controller.project.fixtures) == 1
    controller.remove_fixture(fixture.id)
    assert len(controller.project.fixtures) == 0


def test_fixture_controller_add_replace_remove(ui) -> None:
    from antcam_rc2.core.project.models import Fixture, FixtureKind, Stock

    _core, controller = ui
    controller.new_project(
        "fixtures", "makera_z1", Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061")
    )
    fixture = Fixture(
        id="fix_1234abcd", name="Clamp", kind=FixtureKind.FIXED, width_mm=20.0, length_mm=20.0, height_mm=10.0
    )
    controller.add_fixture(fixture)
    assert controller.project.fixtures == (fixture,)

    edited = fixture.model_copy(update={"name": "Vise", "kind": FixtureKind.VISE, "position_x_mm": 5.0})
    controller.replace_fixture(fixture.id, edited)
    assert controller.project.fixtures[0].name == "Vise"
    assert controller.project.fixtures[0].kind is FixtureKind.VISE
    assert controller.project.fixtures[0].position_x_mm == 5.0

    controller.remove_fixture(fixture.id)
    assert controller.project.fixtures == ()


def test_fixture_dialog_result_roundtrip(qapp) -> None:
    from antcam_rc2.core.project.models import Fixture, FixtureKind
    from antcam_rc2.frontends.pyside.dialogs.fixture_dialog import FixtureDialog

    existing = Fixture(
        id="fix_1234abcd", name="Clamp", kind=FixtureKind.FIXED, width_mm=20.0, length_mm=20.0, height_mm=10.0
    )
    dialog = FixtureDialog(fixture=existing)
    dialog._name.setText("Vise")
    dialog._kind.setCurrentText(FixtureKind.VISE.value)
    dialog._width.setValue(40.0)
    result = dialog.result_fixture()
    assert result.id == existing.id
    assert result.name == "Vise"
    assert result.kind is FixtureKind.VISE
    assert result.width_mm == 40.0

    new_dialog = FixtureDialog()
    new_result = new_dialog.result_fixture()
    assert new_result.id.startswith("fix_")


def test_move_operation_by_clamps_and_reorders(ui) -> None:
    from antcam_rc2.core.project.models import Stock

    _core, controller = ui
    controller.new_project(
        "move", "makera_z1", Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061")
    )
    a = controller.add_operation("profiling", tool_id="end_mill_3_175_2f", cooling_id="aerodust")
    b = controller.add_operation("drill", tool_id="drill_2", cooling_id="aerodust")
    controller.move_operation_by(a, 1)
    assert [op.id for op in controller.project.operations] == [b, a]
    controller.move_operation_by(a, -1)
    assert [op.id for op in controller.project.operations] == [a, b]


def test_toolpath_worker_failure_path(qapp, ui) -> None:
    from pathlib import Path

    from antcam_rc2.core.project.models import Stock

    core, controller = ui
    controller.new_project(
        "fail", "makera_z1", Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061")
    )
    controller.import_geometry(Path(__file__).parent.parent / "data" / "mini_polyline_bulge.dxf")
    # Tapping on Makera Z1 always fails with machine_spindle_sync_required.
    operation_id = controller.add_operation("tapping", tool_id="tap_m3", cooling_id="aerodust")
    controller.update_geometry_refs(operation_id, "0", 0)

    controller.generate_toolpath()
    assert pump_until(qapp, lambda: controller.last_plan is not None)
    assert controller.last_plan is not None
    assert not controller.last_plan.is_executable


def test_controller_requires_open_project(ui) -> None:
    _core, controller = ui
    with pytest.raises(RuntimeError, match="no project"):
        controller.add_operation("drill", tool_id="drill_2", cooling_id="aerodust")


def test_theme_helpers() -> None:
    from antcam_rc2.core.project.models import OperationType
    from antcam_rc2.frontends.pyside import theme

    assert theme.color_to_hex((1.0, 0.0, 0.0, 1.0)) == "#ff0000"
    assert theme.color_to_hex((0.0, 0.0, 0.0, 1.0)) == "#000000"
    assert len(theme.toolpath_color(OperationType.PROFILING)) == 4
    assert theme.toolpath_color(OperationType.POCKETING) != theme.toolpath_color(OperationType.DRILL)
    assert "QMainWindow" in theme.build_qss()
    assert theme.stock_color_for_material_family("wood") == theme.STOCK_COLOR_WOOD
    assert theme.stock_color_for_material_family("aluminum") == theme.STOCK_COLOR_METAL
    assert theme.opaque(theme.STOCK_COLOR_WOOD)[3] == 1.0


def test_lights_matrices_are_consistent() -> None:
    import numpy as np

    from antcam_rc2.core.rendering.scene_graph import RenderBox
    from antcam_rc2.frontends.pyside.viewport.lights import (
        Lights,
        directional_light_matrices,
        look_at,
        orthographic_projection,
    )

    box = RenderBox(min_x=0.0, min_y=0.0, min_z=0.0, max_x=20.0, max_y=20.0, max_z=10.0)
    view, projection = directional_light_matrices(Lights(), box)
    assert view.shape == (4, 4)
    assert projection.shape == (4, 4)
    # The box center must be inside the orthographic frustum.
    center = np.array([10.0, 10.0, 5.0, 1.0])
    clip = projection @ view @ center
    assert abs(clip[0] / clip[3]) < 1.0
    assert abs(clip[1] / clip[3]) < 1.0
    assert clip[2] / clip[3] < 1.0  # inside the near/far range

    eye = np.array([0.0, 0.0, 10.0])
    matrix = look_at(eye, np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]))
    transformed = matrix @ np.array([0.0, 0.0, 0.0, 1.0])
    assert transformed[2] == pytest.approx(-10.0, abs=1e-6)
    assert orthographic_projection(-1, 1, -1, 1, 0.1, 10.0).shape == (4, 4)


def test_event_bridge_full_routing(qapp, ui) -> None:
    from antcam_rc2.core.project.models import Stock

    core, controller = ui
    received: list[str] = []
    bridge = controller.event_bridge
    bridge.machine_changed.connect(lambda _pid, machine_id: received.append(f"machine:{machine_id}"))
    bridge.fixture_added.connect(lambda _pid, fixture_id: received.append(f"fixture:{fixture_id}"))
    bridge.operation_reordered.connect(lambda _pid, op_id, index: received.append(f"reordered:{op_id}:{index}"))

    project = controller.project
    assert project is None
    controller.new_project(
        "bridge", "makera_z1", Stock(width_mm=30, length_mm=20, height_mm=5, material_id="aluminum_6061")
    )
    a = controller.add_operation("drill", tool_id="drill_2", cooling_id="aerodust")
    controller.add_operation("drill", tool_id="drill_2", cooling_id="aerodust")
    controller.move_operation_by(a, 1)
    assert f"reordered:{a}:1" in received
