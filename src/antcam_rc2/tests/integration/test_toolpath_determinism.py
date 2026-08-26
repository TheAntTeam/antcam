"""Determinism guarantees: identical inputs produce identical plan fingerprints."""

from __future__ import annotations

from antcam_rc2.core.geometry.curves import Circle, LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
from antcam_rc2.core.toolpath.settings import PlanningSettings


def build_project_and_scene(application) -> tuple:
    scene = GeometryScene(source=SourceInfo(format="dxf"))
    points = [Point2(0.0, 0.0), Point2(20.0, 0.0), Point2(20.0, 10.0), Point2(0.0, 10.0)]
    scene.add_entity(
        Contour([LineSegment(a, b) for a, b in zip(points, points[1:] + [points[0]], strict=True)]), "profile"
    )
    scene.add_entity(Circle(Point2(10.0, 5.0), 1.5), "holes")
    project = application.project_service.create_project(
        "Determinism fixture",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "profile", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0, stepdown_mm=1.0),
    )
    application.project_service.add_operation(
        project.id,
        OperationType.DRILL,
        tool_id="drill_2",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "holes", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0, stepdown_mm=1.0),
    )
    return project, scene


def test_plan_fingerprint_is_identical_across_repeated_planning(application) -> None:
    project, scene = build_project_and_scene(application)
    settings = PlanningSettings(clearance_z_mm=5.0)

    fingerprints = {
        application.toolpath_service.plan_project(project.id, scene, settings).fingerprint() for _ in range(5)
    }

    assert len(fingerprints) == 1


def test_plan_json_round_trip_preserves_fingerprint(application) -> None:
    project, scene = build_project_and_scene(application)
    settings = PlanningSettings(clearance_z_mm=5.0)
    plan = application.toolpath_service.plan_project(project.id, scene, settings)

    canonical = plan.model_dump_json(exclude=plan.canonical_excludes())
    restored = type(plan).model_validate_json(canonical)

    assert restored.fingerprint() == plan.fingerprint()


def test_plan_is_deterministic_with_different_planning_settings(application) -> None:
    project, scene = build_project_and_scene(application)
    first = application.toolpath_service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=3.0))
    second = application.toolpath_service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=8.0))

    assert first.fingerprint() != second.fingerprint()  # settings are part of the identity


def test_scene_fingerprint_changes_when_geometry_changes(application) -> None:
    from antcam_rc2.core.project.geometry_refs import build_scene_index, invalidate_scene_index_cache

    project, scene = build_project_and_scene(application)
    before = build_scene_index(scene).scene_fingerprint

    # Mutation after attach requires an explicit cache invalidation (the
    # index-cache contract: scenes are immutable after attach).
    scene.layer("profile", create=False).entities[0] = Contour(
        [LineSegment(Point2(0.0, 0.0), Point2(1.0, 0.0)), LineSegment(Point2(1.0, 0.0), Point2(0.0, 0.0))]
    )
    invalidate_scene_index_cache()
    after = build_scene_index(scene).scene_fingerprint

    assert before != after
