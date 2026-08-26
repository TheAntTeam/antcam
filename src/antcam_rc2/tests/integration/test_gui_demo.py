"""GUI demo: the desktop window launches with a bundled sample preloaded."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from antcam_rc2.core.config import AppConfig

SAMPLES_DIR = Path(__file__).parent.parent.parent / "src" / "antcam_rc2" / "samples"


@pytest.fixture()
def offscreen_qt() -> Iterator[None]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield


def _demo_flow(application, controller, sample_path: Path, depth: float) -> int:
    """Replicate the ``gui-demo`` preload: project + geometry + operations + plan."""
    from antcam_rc2.__main__ import _add_demo_operations
    from antcam_rc2.core.io import import_file
    from antcam_rc2.core.project.models import Stock
    from antcam_rc2.core.toolpath.settings import PlanningSettings

    scene = import_file(sample_path)
    box = scene.bounding_box()
    controller.new_project(
        f"demo {sample_path.stem}",
        machine_id="makera_z1",
        stock=Stock(
            width_mm=max(box.width, 1.0) + 10.0,
            length_mm=max(box.height, 1.0) + 10.0,
            height_mm=10.0,
            material_id="aluminum_6061",
        ),
    )
    controller.import_geometry(sample_path)
    project = controller.project
    loaded_scene = controller.scene
    assert project is not None and loaded_scene is not None
    added = _add_demo_operations(application.project_service, project.id, loaded_scene, depth)
    controller.toolpath_controller.generate(project.id, loaded_scene, PlanningSettings(clearance_z_mm=5.0))
    return added


@pytest.mark.parametrize("sample", ["mech_plate.dxf", "pcb_panel.svg", "imperial_part.dxf"])
def test_gui_demo_preloads_sample(offscreen_qt, app_config: AppConfig, sample: str) -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from antcam_rc2.app.application import Application
    from antcam_rc2.frontends.pyside.app_window import MainWindow
    from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController
    from antcam_rc2.frontends.pyside.main import create_application

    sample_path = SAMPLES_DIR / sample
    assert sample_path.is_file(), f"missing bundled sample {sample_path}"

    core = Application(config=app_config)
    try:
        app = QApplication.instance() or create_application()
        controller = ProjectController(core)
        added = _demo_flow(core, controller, sample_path, depth=2.0)
        assert added > 0

        window = MainWindow(controller)
        window.show()

        # The event loop must keep running with the window visible: prove it by
        # scheduling a quit and asserting we reach it.
        exited: list[bool] = []
        QTimer.singleShot(150, lambda: (exited.append(True), app.quit()))
        app.exec()
        assert exited == [True]
        assert controller.project is not None
        assert controller.scene is not None
        assert len(controller.project.operations) == added
        assert controller.last_plan is None or controller.last_plan.is_executable
    finally:
        core.shutdown()


def test_gui_demo_command_registered() -> None:
    from antcam_rc2.__main__ import build_parser

    parser = build_parser()
    # argparse stores the subcommand names in the "command" action's choices
    command_action = next(action for action in parser._actions if action.dest == "command")
    assert "gui-demo" in command_action.choices
    assert "demo" in command_action.choices
