"""Regression tests for the feature-tree Qt model node lifetime.

``QAbstractItemModel.createIndex`` stores the Python object passed as its
internal pointer.  The object must stay alive for as long as any index may be
used, otherwise accessing ``index.internalPointer()`` dereferences freed memory
and the process crashes (this happened as soon as a feature gained an
operation child).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import numpy as np
import pytest

from antcam_rc2.core.geometry3d.mesh import TriMesh
from antcam_rc2.core.geometry3d.scene import (
    Feature3D,
    FeatureKind,
    SolidBody,
    SolidScene,
    SolidSourceInfo,
)
from antcam_rc2.core.project.models import OperationType, Stock
from antcam_rc2.core.project.solid_refs import create_solid_ref


@pytest.fixture()
def qt_app() -> Iterator[None]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    yield


def _solid_scene() -> SolidScene:
    vertices = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 10.0, 0.0], [0.0, 10.0, 0.0]])
    faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    feature = Feature3D(
        kind=FeatureKind.FACE_PLANAR,
        body_index=0,
        feature_index=0,
        boundary=((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)),
        triangles=(0, 1),
        facing=True,
    )
    body = SolidBody(id="body_0", name="plate", mesh=TriMesh(vertices=vertices, faces=faces), features=(feature,))
    return SolidScene(source=SolidSourceInfo(format="stl"), bodies=(body,))


def _build_project_with_feature_operation():
    from antcam_rc2.app.application import Application

    application = Application()
    scene = _solid_scene()
    project = application.project_service.create_project(
        "tree",
        machine_id="makera_z1",
        stock=Stock(width_mm=40.0, length_mm=40.0, height_mm=10.0, material_id="aluminum_6061"),
    )
    application.project_service.attach_solid(project.id, scene, None)
    operation = application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
    )
    application.project_service.replace_solid_refs(project.id, operation.id, (create_solid_ref(scene, 0, 0),))
    return application, application.project_service.get_project(project.id), scene


def test_operation_child_nodes_are_cached_and_stable(qt_app: None) -> None:
    from PySide6.QtCore import QModelIndex

    from antcam_rc2.frontends.pyside.widgets.feature_tree_model import FeatureTreeModel, FeatureTreeNode

    application, project, scene = _build_project_with_feature_operation()
    try:
        model = FeatureTreeModel()
        model.set_data(project, scene, None)

        stock = model.index(0, 0, QModelIndex())
        feature = model.index(0, 0, stock)
        assert stock.isValid() and feature.isValid()

        first = model.index(0, 0, feature)
        second = model.index(0, 0, feature)
        assert first.isValid() and second.isValid()

        first_node = first.internalPointer()
        second_node = second.internalPointer()
        assert isinstance(first_node, FeatureTreeNode)
        # Same Python object both times: the model retains it, so no dangling
        # pointer can be handed to the view.
        assert first_node is second_node
        assert first_node.kind.value == "operation"
        assert first_node.operation_id is not None

        # The node is really owned by the model.
        assert model._operation_nodes[first_node.operation_id] is first_node
        # And the tree exposes exactly one operation under the feature.
        assert model.rowCount(feature) == 1
    finally:
        application.shutdown()


def test_model_reset_invalidates_operation_node_cache(qt_app: None) -> None:
    from PySide6.QtCore import QModelIndex

    from antcam_rc2.frontends.pyside.widgets.feature_tree_model import FeatureTreeModel

    application, project, scene = _build_project_with_feature_operation()
    try:
        model = FeatureTreeModel()
        model.set_data(project, scene, None)
        assert len(model._operation_nodes) == 0  # built lazily on first query

        stock = model.index(0, 0, QModelIndex())
        feature = model.index(0, 0, stock)
        assert model.index(0, 0, feature).isValid()
        assert len(model._operation_nodes) == 1

        model.set_data(project, scene, None)
        assert len(model._operation_nodes) == 0
    finally:
        application.shutdown()
