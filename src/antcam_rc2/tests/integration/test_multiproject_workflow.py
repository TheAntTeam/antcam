"""Workflow hardening: multi-project, undo/redo stress, round-trip, catalog mismatch."""

from __future__ import annotations

from pathlib import Path

import pytest

from antcam_rc2.app.application import Application
from antcam_rc2.core.feeds_speeds.calculator import FeedsSpeedsCalculator
from antcam_rc2.core.geometry.curves import Circle, LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.operations.registry import build_standard_registry
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import GeometryRef, OperationParameters, OperationType, Stock
from antcam_rc2.core.toolpath.models import OperationPlanStatus
from antcam_rc2.core.toolpath.service import PlanningSettings, ToolpathService


def make_scene() -> GeometryScene:
    scene = GeometryScene(source=SourceInfo(format="dxf", path="workflow.dxf"))
    points = [Point2(0.0, 0.0), Point2(20.0, 0.0), Point2(20.0, 10.0), Point2(0.0, 10.0)]
    scene.add_entity(
        Contour([LineSegment(a, b) for a, b in zip(points, points[1:] + [points[0]], strict=True)]),
        "profile",
    )
    scene.add_entity(Circle(Point2(10.0, 5.0), 1.5), "holes")
    return scene


def _service(application: Application) -> ToolpathService:
    return ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )


def _build_project(application: Application, name: str, operations: int) -> str:
    project = application.project_service.create_project(
        name,
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    for index in range(operations):
        kind = OperationType.PROFILING if index % 2 == 0 else OperationType.DRILL
        layer = "profile" if kind is OperationType.PROFILING else "holes"
        application.project_service.add_operation(
            project.id,
            kind,
            tool_id="end_mill_3_175_2f" if kind is OperationType.PROFILING else "drill_2",
            cooling_id="aerodust",
            geometry_refs=(create_geometry_ref(scene, layer, 0),),
            operation_parameters=OperationParameters(depth_mm=2.0, stepdown_mm=1.0),
        )
    return project.id


def test_five_projects_with_twenty_operations_each_plan_independently(application: Application) -> None:
    service = _service(application)
    scenes = {name: make_scene() for name in range(5)}
    ids = [_build_project(application, f"plate-{i}", 20) for i in range(5)]
    fingerprints = set()
    for project_id in ids:
        scene = scenes[len(fingerprints)]
        snapshot = application.project_service.get_project(project_id)
        plan = service.plan_snapshot(snapshot, scene, PlanningSettings(clearance_z_mm=5.0))
        assert plan.is_executable
        assert len(plan.operations) == 20
        assert all(op.status.value == "succeeded" for op in plan.operations)
        fingerprints.add(plan.fingerprint())
    assert len(fingerprints) == 5  # identical setups must still be distinct projects


def test_undo_redo_stress_hundred_mutations(application: Application) -> None:
    project_id = _build_project(application, "undo-stress", 2)
    operations = application.project_service.list_operations(project_id)

    # Mutate: 100 toggles/parameter edits; the bounded stack keeps only the
    # most recent max_depth commands.
    for index in range(100):
        operation = operations[index % len(operations)]
        if index % 2 == 0:
            application.project_service.toggle_operation(project_id, operation.id, index % 4 != 0)
        else:
            application.project_service.replace_operation_parameters(
                project_id,
                operation.id,
                OperationParameters(depth_mm=2.0 + (index % 5) * 0.5, stepdown_mm=1.0),
            )
    assert application.command_stack.undo_count == application.command_stack.max_depth

    # Unwinding the retained history restores the state before the last 50
    # commands: 25 toggles on operation[0] -> it ends disabled.
    for _ in range(application.command_stack.max_depth):
        assert application.project_service.undo()
    assert application.command_stack.undo_count == 0
    restored = application.project_service.get_project(project_id)
    assert [op.enabled for op in restored.operations] == [False, True]

    # Redo everything; state matches the last mutation.
    for _ in range(application.command_stack.max_depth):
        assert application.project_service.redo()
    final = application.project_service.get_project(project_id)
    assert application.command_stack.undo_count == application.command_stack.max_depth
    # Last command (index 99, odd) edited operation[1]'s depth: 2.0 + (99 % 5) * 0.5
    assert final.operations[1].parameters.depth_mm == pytest.approx(2.0 + (99 % 5) * 0.5)


def test_undo_stack_is_bounded(application: Application) -> None:
    project_id = _build_project(application, "bounded", 1)
    operation = application.project_service.list_operations(project_id)[0]
    for index in range(application.command_stack.max_depth + 40):
        application.project_service.toggle_operation(project_id, operation.id, index % 2 == 0)
    assert application.command_stack.undo_count <= application.command_stack.max_depth


def _fresh_application(application: Application) -> Application:
    """A second Application sharing the same catalog files (fresh repository)."""
    return Application(config=application.config)


def test_export_import_round_trip_preserves_operations(application: Application, tmp_path: Path) -> None:
    project_id = _build_project(application, "roundtrip", 3)
    destination = tmp_path / "project.acproj"
    application.project_service.export_project(project_id, destination)
    fresh = _fresh_application(application)
    imported = fresh.project_service.import_project(destination)
    assert imported.name == "roundtrip"
    assert len(imported.operations) == 3
    plan = _service(fresh).plan_project(imported.id, make_scene(), PlanningSettings(clearance_z_mm=5.0))
    assert plan.is_executable
    fresh.shutdown()


def test_import_with_catalog_version_mismatch_still_loads(application: Application, tmp_path: Path) -> None:
    project_id = _build_project(application, "versioned", 1)
    destination = tmp_path / "versioned.acproj"
    application.project_service.export_project(project_id, destination)
    # Corrupt the stored catalog versions (simulate an upgraded catalog).
    text = destination.read_text(encoding="utf-8")
    text = text.replace('"tools": "', '"tools": "stale-', 1)
    destination.write_text(text, encoding="utf-8")
    fresh = _fresh_application(application)
    imported = fresh.project_service.import_project(destination)
    assert len(imported.operations) == 1  # loads despite the mismatch
    fresh.shutdown()


def test_non_executable_plan_has_failed_status(application: Application) -> None:
    project = application.project_service.create_project(
        "broken",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    # Reference a layer that does not exist -> plan must FAIL, not crash.
    ghost_ref = GeometryRef(
        layer_name="ghost",
        entity_index=0,
        entity_type="LineSegment",
        entity_fingerprint="sha256:" + "0" * 64,
    )
    application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(ghost_ref,),
        operation_parameters=OperationParameters(depth_mm=2.0),
    )
    plan = _service(application).plan_project(project.id, scene, PlanningSettings(clearance_z_mm=5.0))
    assert plan.operations[0].status is OperationPlanStatus.FAILED
    assert not plan.is_executable
