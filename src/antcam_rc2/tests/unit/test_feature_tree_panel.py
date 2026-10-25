"""Feature-tree panel: embedded parameters and refresh stability.

The operation parameters live inside the Features tab (right-hand side) and
must follow the tree selection.  Refreshing the tree — which happens on every
``scene_changed`` emission, including feature-selection highlights — must not
reset it or lose the selected operation.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import numpy as np
import pytest

from antcam_rc2.app.application import Application
from antcam_rc2.core.geometry.curves import Circle
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.geometry3d.mesh import TriMesh
from antcam_rc2.core.geometry3d.scene import (
    Feature3D,
    FeatureKind,
    SolidBody,
    SolidScene,
    SolidSourceInfo,
)
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
from antcam_rc2.core.project.solid_refs import create_solid_ref


@pytest.fixture()
def qt_app() -> Iterator[None]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    yield


def _scene() -> GeometryScene:
    scene = GeometryScene(source=SourceInfo(format="dxf", path="mini.dxf"))
    scene.add_entity(Circle(Point2(0.0, 0.0), 5.0), "holes")
    scene.add_entity(Circle(Point2(20.0, 0.0), 3.0), "holes")
    return scene


def _make_panel():
    from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController
    from antcam_rc2.frontends.pyside.panels.feature_tree_panel import FeatureTreePanel

    application = Application()
    controller = ProjectController(application)
    controller.new_project(
        "tree",
        machine_id="makera_z1",
        stock=Stock(width_mm=60.0, length_mm=60.0, height_mm=10.0, material_id="aluminum_6061"),
    )
    assert controller.project is not None
    scene = _scene()
    application.project_service.attach_geometry(controller.project.id, scene)
    operation = application.project_service.add_operation(
        controller.project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "holes", 0),),
        operation_parameters=OperationParameters(depth_mm=1.0),
    )
    panel = FeatureTreePanel(controller)
    panel.refresh()
    return application, controller, panel, operation


def _solid_scene_with_two_features() -> SolidScene:
    vertices = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 10.0, 0.0], [0.0, 10.0, 0.0]])
    faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    first = Feature3D(
        kind=FeatureKind.FACE_PLANAR,
        body_index=0,
        feature_index=0,
        boundary=((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)),
        triangles=(0, 1),
        facing=True,
    )
    second = Feature3D(
        kind=FeatureKind.FACE_PLANAR,
        body_index=0,
        feature_index=1,
        boundary=((0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (5.0, 5.0, 0.0), (0.0, 5.0, 0.0)),
        triangles=(0,),
        facing=True,
    )
    body = SolidBody(id="body_0", name="plate", mesh=TriMesh(vertices=vertices, faces=faces), features=(first, second))
    return SolidScene(source=SolidSourceInfo(format="stl"), bodies=(body,))


def test_clicking_another_feature_does_not_move_the_operation(qt_app: None) -> None:
    from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController
    from antcam_rc2.frontends.pyside.panels.feature_tree_panel import FeatureTreePanel

    application = Application()
    try:
        controller = ProjectController(application)
        controller.new_project(
            "move",
            machine_id="makera_z1",
            stock=Stock(width_mm=60.0, length_mm=60.0, height_mm=10.0, material_id="aluminum_6061"),
        )
        assert controller.project is not None
        scene = _solid_scene_with_two_features()
        application.project_service.attach_solid(controller.project.id, scene, None)
        operation = application.project_service.add_operation(
            controller.project.id,
            OperationType.PROFILING,
            tool_id="end_mill_3_175_2f",
            cooling_id="aerodust",
            solid_refs=(create_solid_ref(scene, 0, 0),),
            operation_parameters=OperationParameters(depth_mm=1.0),
        )
        panel = FeatureTreePanel(controller)
        panel.refresh()

        # The operation is active (as right after creating it for feature 0).
        panel._tree.setCurrentIndex(panel._model.index_for_operation(operation.id))
        assert controller.selected_operation_id == operation.id

        # Clicking a *different* feature must not re-target the operation.
        panel._tree.setCurrentIndex(panel._model.index_for_feature(body_index=0, feature_index=1))

        snapshot = application.project_service.get_project(controller.project.id)
        stored = next(candidate for candidate in snapshot.operations if candidate.id == operation.id)
        assert [(ref.body_index, ref.feature_index) for ref in stored.solid_refs] == [(0, 0)]
        assert controller.selected_operation_id is None
    finally:
        application.shutdown()


def test_parameters_panel_is_disabled_until_an_operation_is_selected(qt_app: None) -> None:
    application, _controller, panel, _operation = _make_panel()
    try:
        assert panel._parameters_panel is not None
        # Nothing selected yet -> the form is disabled (not a dead tab).
        assert not panel._parameters_panel.isEnabled()
    finally:
        application.shutdown()


def test_selecting_an_operation_enables_its_parameters(qt_app: None) -> None:
    application, controller, panel, operation = _make_panel()
    try:
        index = panel._model.index_for_operation(operation.id)
        assert index.isValid()
        panel._tree.setCurrentIndex(index)

        assert controller.selected_operation_id == operation.id
        assert panel._parameters_panel.isEnabled()
        assert panel._parameters_panel._editing_operation_id == operation.id
    finally:
        application.shutdown()


def test_redundant_refresh_keeps_the_selected_operation(qt_app: None) -> None:
    application, controller, panel, operation = _make_panel()
    try:
        index = panel._model.index_for_operation(operation.id)
        panel._tree.setCurrentIndex(index)
        assert controller.selected_operation_id == operation.id

        # ``scene_changed`` fires on every feature highlight; a refresh with
        # unchanged inputs must be a no-op and keep the selection.
        panel.refresh()

        current = panel._tree.currentIndex()
        assert current.isValid()
        assert current.internalPointer().operation_id == operation.id
        assert controller.selected_operation_id == operation.id
    finally:
        application.shutdown()


def test_rebuild_after_operation_change_restores_the_selection(qt_app: None) -> None:
    application, controller, panel, operation = _make_panel()
    try:
        panel._tree.setCurrentIndex(panel._model.index_for_operation(operation.id))

        # A real mutation creates a new project object -> the tree rebuilds.
        application.project_service.add_operation(
            controller.project.id,
            OperationType.DRILL,
            tool_id="drill_2",
            cooling_id="aerodust",
        )
        panel.refresh()

        current = panel._tree.currentIndex()
        assert current.isValid()
        assert current.internalPointer().operation_id == operation.id
    finally:
        application.shutdown()
