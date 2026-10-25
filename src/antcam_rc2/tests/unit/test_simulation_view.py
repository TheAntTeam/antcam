"""Simulation viewport wiring: voxel mesh on its own pipeline, reset keeps the part.

The voxel mesh used to be uploaded to the same ``mesh`` pipeline as the render
scene, so the simulated material overwrote the part and clearing it on "Reset"
wiped the part out of the viewport.  These tests pin the separation without
needing a GL context (the uploads are recorded).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import numpy as np
import pytest

from antcam_rc2.app.application import Application
from antcam_rc2.core.project.models import Stock


@pytest.fixture()
def qt_app() -> Iterator[None]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    yield


def test_voxel_mesh_uses_a_dedicated_pipeline() -> None:
    from antcam_rc2.core.rendering.scene_graph import RenderMesh, RenderScene
    from antcam_rc2.frontends.pyside.viewport.renderer import Renderer

    renderer = Renderer()
    recorded: list[str] = []
    renderer._upload = lambda name, data, *, stride, layout: recorded.append(name)  # type: ignore[method-assign]
    renderer._upload_grid = lambda data: None  # type: ignore[method-assign]
    renderer._upload_axes = lambda data: None  # type: ignore[method-assign]
    renderer._upload_ground = lambda box: None  # type: ignore[method-assign]

    scene = RenderScene(meshes=(RenderMesh(vertices=(0, 0, 0, 1, 0, 0, 0, 1, 0), triangles=(0, 1, 2)),))
    renderer.set_scene(scene, None)
    renderer.set_voxel_mesh(
        np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), np.array([[0, 1, 2]]), (1, 1, 1, 1)
    )

    assert "mesh" in recorded  # the part
    assert "voxel" in recorded  # the simulated material
    assert "voxel" in Renderer._PIPELINES

    # Clearing the simulation (Reset) must touch only the voxel pipeline.
    recorded.clear()
    renderer.set_voxel_mesh(np.empty((0, 3)), np.empty((0, 3), dtype=np.int64), (1, 1, 1, 1))
    assert recorded == ["voxel"]


class _FakeViewport:
    def __init__(self) -> None:
        self.voxel_meshes: list[tuple] = []
        self.markers: list = []
        self.simulation_active: list[bool] = []

    def set_voxel_mesh(self, vertices, triangles, color) -> None:
        self.voxel_meshes.append((np.asarray(vertices), np.asarray(triangles), color))

    def set_tool_marker(self, position, mesh=None) -> None:
        self.markers.append(position)

    def set_simulation_active(self, active: bool) -> None:
        self.simulation_active.append(active)


def _controller_with_viewport():
    from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController

    application = Application()
    controller = ProjectController(application)
    controller.new_project(
        "sim",
        machine_id="makera_z1",
        stock=Stock(width_mm=10.0, length_mm=10.0, height_mm=4.0, material_id="aluminum_6061"),
    )
    viewport = _FakeViewport()
    controller.bind_viewport(viewport)
    return application, controller, viewport


def test_update_simulation_view_pushes_a_voxel_mesh_and_marker(qt_app: None) -> None:
    from antcam_rc2.frontends.pyside import theme

    application, controller, viewport = _controller_with_viewport()
    try:
        mask = np.zeros((4, 4, 4), dtype=bool)
        mask[1:3, 1:3, 1:3] = True
        controller.update_simulation_view(mask, 1.0, (1.0, 2.0, 3.0), 0)

        assert viewport.voxel_meshes
        vertices, triangles, color = viewport.voxel_meshes[-1]
        assert vertices.shape[0] > 0
        assert triangles.shape[0] > 0
        assert color == theme.SIMULATED_MATERIAL_COLOR
        assert viewport.markers[-1] == (1.0, 2.0, 3.0)
        # The viewport switches to the simulation look (translucent stock hidden).
        assert viewport.simulation_active[-1] is True
    finally:
        application.shutdown()


class _FakeProgram:
    def __init__(self) -> None:
        self.enabled: list[int] = []
        self.attrs: list[tuple] = []

    def bind(self) -> None: ...

    def release(self) -> None: ...

    def enableAttributeArray(self, location: int) -> None:
        self.enabled.append(location)

    def setAttributeBuffer(self, location: int, *args) -> None:
        self.attrs.append((location, *args))


class _FakeBuffer:
    def __init__(self) -> None:
        self.bytes = 0

    def bind(self) -> None: ...

    def release(self) -> None: ...

    def allocate(self, data, size=None) -> None:
        self.bytes = len(data)


class _FakeVAO:
    def bind(self) -> None: ...

    def release(self) -> None: ...


def test_set_voxel_mesh_uploads_lit_voxel_geometry() -> None:
    from antcam_rc2.frontends.pyside.viewport.renderer import Renderer

    renderer = Renderer()
    line = _FakeProgram()
    solid = _FakeProgram()
    renderer._programs = {"line": line, "solid": solid}  # type: ignore[assignment]
    renderer._vbos = {"voxel": _FakeBuffer()}  # type: ignore[assignment]
    renderer._vaos = {"voxel": _FakeVAO()}  # type: ignore[assignment]
    renderer._counts = {}

    renderer.set_voxel_mesh(
        np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
        np.array([[0, 1, 2]]),
        (1.0, 0.47, 0.02, 1.0),
    )
    # 3 vertices, lit pipeline (position + normal + color) so the carved
    # pocket is shaded and readable.
    assert renderer._counts["voxel"] == 3
    assert sorted(solid.enabled) == [0, 1, 2]
    assert solid.attrs and solid.attrs[0][0] == 0
    assert line.enabled == []


def test_voxel_and_marker_are_lit() -> None:
    from antcam_rc2.frontends.pyside.viewport.renderer import Renderer

    assert "voxel" in Renderer._PIPELINES
    # Both the simulated material and the tool marker use the lit solid program.
    assert "voxel" in Renderer._SOLID_PIPELINES
    assert "marker" in Renderer._SOLID_PIPELINES


def test_renderer_hides_the_stock_when_simulating() -> None:
    from antcam_rc2.frontends.pyside.viewport.renderer import Renderer

    renderer = Renderer()
    assert renderer._stock_visible is True
    renderer.set_stock_visible(False)
    assert renderer._stock_visible is False
    renderer.set_stock_visible(True)
    assert renderer._stock_visible is True


def test_entering_the_simulation_tab_shows_the_full_opaque_stock(qt_app: None) -> None:
    from antcam_rc2.frontends.pyside import theme
    from antcam_rc2.frontends.pyside.panels.simulation_panel import SimulationPanel

    application, controller, viewport = _controller_with_viewport()
    try:
        panel = SimulationPanel(controller)
        assert not viewport.voxel_meshes  # nothing shown yet

        panel.enter()  # user opens the Simulation tab
        assert viewport.voxel_meshes
        vertices, triangles, color = viewport.voxel_meshes[-1]
        assert vertices.shape[0] > 0
        assert triangles.shape[0] > 0
        assert color == theme.SIMULATED_MATERIAL_COLOR
        assert viewport.simulation_active[-1] is True

        panel.leave()  # user switches away
        assert viewport.simulation_active[-1] is False
        vertices, triangles, _color = viewport.voxel_meshes[-1]
        assert vertices.shape[0] == 0
        assert triangles.shape[0] == 0
    finally:
        application.shutdown()


def test_simulate_button_follows_toolpath_availability(qt_app: None) -> None:
    from types import SimpleNamespace

    from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController
    from antcam_rc2.frontends.pyside.panels.simulation_panel import SimulationPanel

    application = Application()
    try:
        controller = ProjectController(application)
        controller.new_project(
            "sim",
            machine_id="makera_z1",
            stock=Stock(width_mm=30.0, length_mm=30.0, height_mm=10.0, material_id="aluminum_6061"),
        )
        panel = SimulationPanel(controller)
        # No plan yet: the button is disabled (this is how project_opened left it).
        assert not panel._run_button.isEnabled()

        # Generating a toolpath emits toolpath_changed -> the button enables.
        controller._last_plan = SimpleNamespace(is_executable=True)
        controller.toolpath_changed.emit()
        assert panel._run_button.isEnabled()

        controller._last_plan = None
        controller.toolpath_changed.emit()
        assert not panel._run_button.isEnabled()
    finally:
        application.shutdown()


def test_clear_simulation_only_clears_the_voxel(qt_app: None) -> None:
    application, controller, viewport = _controller_with_viewport()
    try:
        mask = np.ones((4, 4, 4), dtype=bool)
        controller.update_simulation_view(mask, 1.0, None, 0)
        controller.clear_simulation()

        vertices, triangles, _color = viewport.voxel_meshes[-1]
        assert vertices.shape[0] == 0
        assert triangles.shape[0] == 0
        assert viewport.markers[-1] is None
        assert viewport.simulation_active[-1] is False
    finally:
        application.shutdown()
