"""Placement must not stale references or break planning.

References (2D geometry refs, 3D solid refs) and the geometry binding are
fingerprinted on absolute coordinates, so they are stored against the scenes
*as imported* (unplaced).  Planning therefore resolves refs on the unplaced
scenes and applies the project placements afterwards.  Before this, the GUI
passed the already-placed scenes and every operation failed with a "stale
solid reference" / "binding mismatch" error.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from antcam_rc2.app.application import Application
from antcam_rc2.core.feeds_speeds.calculator import FeedsSpeedsCalculator
from antcam_rc2.core.io import import_file
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.io3d.step import import_step
from antcam_rc2.core.operations.registry import build_standard_registry
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import (
    GeometryPlacement,
    OperationParameters,
    OperationType,
    SolidPlacement,
    Stock,
)
from antcam_rc2.core.project.solid_refs import create_solid_ref
from antcam_rc2.core.toolpath.service import PlanningSettings, ToolpathService

_DATA = Path(__file__).parent.parent / "data"
_CORPUS = Path(__file__).parent.parent / "corpus" / "dxf"


@pytest.fixture()
def qt_app() -> Iterator[None]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    yield


def _service(application: Application) -> ToolpathService:
    return ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )


def _new_project(application: Application, name: str):
    return application.project_service.create_project(
        name,
        machine_id="makera_z1",
        stock=Stock(width_mm=200.0, length_mm=200.0, height_mm=20.0, material_id="aluminum_6061"),
    )


def _plan_solid(application: Application, placement: SolidPlacement | None):
    scene = import_step(str(_DATA / "flange.step"))
    perimeter = next(feature for feature in scene.bodies[0].features if feature.kind.value == "perimeter")
    project = _new_project(application, f"solid_{placement is not None}")
    application.project_service.attach_solid(project.id, scene, placement)
    application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        solid_refs=(create_solid_ref(scene, 0, perimeter.feature_index),),
        operation_parameters=OperationParameters(depth_mm=2.0, stepdown_mm=1.0),
    )
    snapshot = application.project_service.get_project(project.id)
    empty = GeometryScene(source=SourceInfo(format="dxf", path=""))
    # The scene is passed unplaced, exactly as the controller now does.
    plan = _service(application).plan_snapshot(snapshot, empty, PlanningSettings(clearance_z_mm=5.0), solid_scene=scene)
    return plan.operations[-1]


def test_solid_placement_keeps_refs_valid_and_shifts_the_toolpath() -> None:
    application = Application()
    try:
        unplaced = _plan_solid(application, None)
        placed = _plan_solid(application, SolidPlacement(translation=(5.0, 7.0, 0.0)))

        assert unplaced.status.value == "succeeded"
        assert placed.status.value == "succeeded"  # not "stale solid reference"

        assert unplaced.program is not None and placed.program is not None
        xs_unplaced = [motion.endpoint.x_mm for motion in unplaced.program.motions]
        ys_unplaced = [motion.endpoint.y_mm for motion in unplaced.program.motions]
        xs_placed = [motion.endpoint.x_mm for motion in placed.program.motions]
        ys_placed = [motion.endpoint.y_mm for motion in placed.program.motions]

        # Everything moved by exactly the placement translation.
        assert min(xs_placed) - min(xs_unplaced) == pytest.approx(5.0)
        assert min(ys_placed) - min(ys_unplaced) == pytest.approx(7.0)
    finally:
        application.shutdown()


def test_plan_ready_renders_a_toolpath_for_a_solid_only_project(qt_app: None) -> None:
    from antcam_rc2.core.rendering.scene_graph import NodeKind
    from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController

    application = Application()
    try:
        controller = ProjectController(application)
        controller.new_project(
            "solid only",
            machine_id="makera_z1",
            stock=Stock(width_mm=200.0, length_mm=200.0, height_mm=20.0, material_id="aluminum_6061"),
        )
        assert controller.project is not None
        scene = import_step(str(_DATA / "flange.step"))
        application.project_service.attach_solid(controller.project.id, scene, None)
        perimeter = next(feature for feature in scene.bodies[0].features if feature.kind.value == "perimeter")
        application.project_service.add_operation(
            controller.project.id,
            OperationType.PROFILING,
            tool_id="end_mill_3_175_2f",
            cooling_id="aerodust",
            solid_refs=(create_solid_ref(scene, 0, perimeter.feature_index),),
            operation_parameters=OperationParameters(depth_mm=2.0, stepdown_mm=1.0),
        )
        snapshot = application.project_service.get_project(controller.project.id)
        empty = GeometryScene(source=SourceInfo(format="dxf", path=""))
        plan = _service(application).plan_snapshot(
            snapshot, empty, PlanningSettings(clearance_z_mm=5.0), solid_scene=scene
        )
        assert plan.is_executable

        # No 2D geometry is attached: the toolpath must still be rendered.
        assert controller.scene is None
        controller._on_plan_ready(plan)
        toolpath_nodes = [node for node in controller.render_scene().nodes if node.kind is NodeKind.TOOLPATH_STRIP]
        assert toolpath_nodes
    finally:
        application.shutdown()


def test_geometry_placement_keeps_the_binding_valid() -> None:
    application = Application()
    try:
        scene = import_file(_CORPUS / "mech_plate.dxf")
        project = _new_project(application, "geom")
        application.project_service.attach_geometry(
            project.id, scene, GeometryPlacement(offset_x_mm=3.0, offset_y_mm=2.0, auto_center_xy=False)
        )
        layer = scene.layers[0]
        application.project_service.add_operation(
            project.id,
            OperationType.PROFILING,
            tool_id="end_mill_3_175_2f",
            cooling_id="aerodust",
            geometry_refs=(create_geometry_ref(scene, layer.name, 0),),
            operation_parameters=OperationParameters(depth_mm=1.0, stepdown_mm=1.0),
        )
        snapshot = application.project_service.get_project(project.id)
        plan = _service(application).plan_snapshot(snapshot, scene, PlanningSettings(clearance_z_mm=5.0))
        result = plan.operations[-1]
        assert result.status.value == "succeeded"
        assert all(diagnostic.code != "geometry_binding_mismatch" for diagnostic in result.diagnostics)
    finally:
        application.shutdown()
