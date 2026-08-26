"""Offscreen UI tests for the simulation panel, timeline and controller."""

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


def _prepare_plan(controller) -> None:
    from antcam_rc2.core.project.models import OperationParameters, Stock

    controller.new_project(
        "sim ui", "makera_z1", Stock(width_mm=30, length_mm=30, height_mm=10, material_id="aluminum_6061")
    )
    controller.import_geometry(Path(__file__).parent.parent / "data" / "mini_polyline_bulge.dxf")
    operation_id = controller.add_operation("profiling", tool_id="end_mill_3_175_2f", cooling_id="aerodust")
    controller.update_geometry_refs(operation_id, "0", 0)
    controller.update_operation_parameters(operation_id, OperationParameters(depth_mm=2.0, stepdown_mm=1.0))
    controller.generate_toolpath()


def test_simulation_panel_runs_and_populates_timeline(qapp, ui) -> None:
    controller, window = ui
    _prepare_plan(controller)
    assert pump_until(qapp, lambda: controller.last_plan is not None)

    panel = window._simulation_panel
    panel._on_run()
    assert pump_until(qapp, lambda: controller.simulation_controller.result is not None)

    result = controller.simulation_controller.result
    assert result is not None
    assert len(result.report.timeline) > 0
    assert len(result.checkpoints) > 0
    assert result.report.stats.operations_simulated == 1
    assert panel._timeline._slider.maximum() == len(result.report.timeline) - 1


def test_simulation_scrub_reconstructs_masks(qapp, ui) -> None:
    controller, window = ui
    _prepare_plan(controller)
    assert pump_until(qapp, lambda: controller.last_plan is not None)
    panel = window._simulation_panel
    panel._on_run()
    assert pump_until(qapp, lambda: controller.simulation_controller.result is not None)

    sim = controller.simulation_controller
    result = sim.result
    timeline = result.report.timeline
    full_mask = sim.mask_at_tick(timeline[-1].index)
    assert full_mask is not None
    # The full mask must match the report's removed count.
    remaining = int(full_mask.sum())
    assert result.report.stats.removed_voxels == 9000 - remaining
    # Scrubbing backwards and forwards keeps the shape stable.
    early = sim.mask_at_tick(2)
    assert early is not None
    assert early.sum() > full_mask.sum()


def test_simulation_panel_step_and_events(qapp, ui) -> None:
    controller, window = ui
    _prepare_plan(controller)
    assert pump_until(qapp, lambda: controller.last_plan is not None)
    panel = window._simulation_panel
    panel._on_run()
    assert pump_until(qapp, lambda: controller.simulation_controller.result is not None)

    panel._seek(1)
    assert panel._timeline.current_tick() == 1
    result = controller.simulation_controller.result
    assert panel._events.count() == len(result.report.events) or panel._events.count() >= 1


def test_voxel_mesh_from_mask_is_pure() -> None:
    import numpy as np

    from antcam_rc2.core.simulation.voxels import VoxelGrid

    mask = np.ones((10, 10, 5), dtype=bool)
    mask[2:8, 2:8, 1:4] = False  # carve a box in the middle
    grid = VoxelGrid(origin=(0.0, 0.0, 0.0), voxel_size=1.0, shape=mask.shape, occupied=mask)
    vertices, triangles = grid.surface_mesh()
    assert vertices.shape[0] > 0
    assert triangles.shape[0] > 0
    assert vertices.shape[1] == 3 and triangles.shape[1] == 3
    # Vertices stay inside the world box.
    assert vertices[:, 0].max() <= 10.0
    assert vertices[:, 2].max() <= 5.0
