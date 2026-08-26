"""Integration tests for Phase 4 headless project-to-toolpath planning."""

from __future__ import annotations

import pytest

from antcam_rc2.app.application import Application
from antcam_rc2.core.feeds_speeds.calculator import FeedsSpeedsCalculator
from antcam_rc2.core.geometry.curves import Circle, LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.operations.registry import build_standard_registry
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
from antcam_rc2.core.toolpath.models import MotionKind, OperationPlanStatus
from antcam_rc2.core.toolpath.service import PlanningSettings, ToolpathService


def make_scene() -> GeometryScene:
    scene = GeometryScene(source=SourceInfo(format="dxf", path="fixture.dxf"))
    points = [Point2(0.0, 0.0), Point2(20.0, 0.0), Point2(20.0, 10.0), Point2(0.0, 10.0)]
    scene.add_entity(
        Contour([LineSegment(start, end) for start, end in zip(points, points[1:] + [points[0]], strict=True)]),
        "profile",
    )
    scene.add_entity(Circle(Point2(10.0, 5.0), 1.5), "holes")
    return scene


def test_service_generates_project_ordered_profile_and_drill_motion(application: Application) -> None:
    project = application.project_service.create_project(
        "Fixture plate",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    profile = application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "profile", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0, stepdown_mm=1.0),
    )
    drill = application.project_service.add_operation(
        project.id,
        OperationType.DRILL,
        tool_id="drill_2",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "holes", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0, stepdown_mm=1.0),
    )
    service = ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )

    plan = service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=5.0))

    assert plan.is_executable
    assert [result.operation_id for result in plan.operations] == [profile.id, drill.id]
    assert all(result.status is OperationPlanStatus.SUCCEEDED for result in plan.operations)
    assert plan.operations[0].program is not None
    assert any(motion.kind is MotionKind.CUT_LINEAR for motion in plan.operations[0].program.motions)
    assert plan.operations[1].pass_count == 2


def test_service_skips_disabled_operations_and_fails_closed_for_stale_geometry(application: Application) -> None:
    project = application.project_service.create_project(
        "Fixture plate",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    operation = application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "profile", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0),
    )
    service = ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )

    application.project_service.toggle_operation(project.id, operation.id, False)
    disabled_plan = service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=5.0))
    assert disabled_plan.operations[0].status is OperationPlanStatus.SKIPPED_DISABLED
    assert disabled_plan.is_executable

    application.project_service.toggle_operation(project.id, operation.id, True)
    scene.layer("profile", create=False).entities[0] = LineSegment(Point2(0.0, 0.0), Point2(1.0, 0.0))
    stale_plan = service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=5.0))
    assert stale_plan.operations[0].status is OperationPlanStatus.FAILED
    assert not stale_plan.is_executable


def test_service_is_deterministic_and_gates_tapping_on_pitch_and_machine_sync(application: Application) -> None:
    project = application.project_service.create_project(
        "Fixture plate",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "profile", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0),
    )
    application.project_service.add_operation(
        project.id,
        OperationType.TAPPING,
        tool_id="tap_m3",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "holes", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0, strategy_parameters={"pitch_mm": 0.5}),
    )
    service = ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )

    first = service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=5.0))
    second = service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=5.0))

    assert first.fingerprint() == second.fingerprint()
    assert first.operations[1].status is OperationPlanStatus.FAILED
    assert first.operations[1].diagnostics[0].code == "machine_spindle_sync_required"
    assert not first.is_executable


def test_service_rejects_tapping_without_pitch_as_param_invalid(application: Application) -> None:
    project = application.project_service.create_project(
        "Fixture plate",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.TAPPING,
        tool_id="tap_m3",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "holes", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0),
    )
    service = ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )

    result = service.plan_operation(
        project.id,
        application.project_service.list_operations(project.id)[0].id,
        scene,
        PlanningSettings(clearance_z_mm=5.0),
    )

    assert result.status is OperationPlanStatus.FAILED
    assert result.diagnostics[0].code == "operation_param_invalid"


def test_export_artifact_binds_plan_to_project_and_scene(application: Application) -> None:
    project, scene = _scaffold(application, (OperationType.PROFILING,))
    artifact = application.toolpath_service.export_artifact(project.id, scene, PlanningSettings(clearance_z_mm=5.0))

    assert artifact.is_valid
    assert artifact.project_id == project.id
    assert artifact.scene_fingerprint.startswith("sha256:")
    assert artifact.project_fingerprint.startswith("sha256:")
    assert artifact.plan.is_executable

    restored = type(artifact).model_validate_json(artifact.model_dump_json(exclude=artifact.canonical_excludes()))
    assert restored.plan.fingerprint() == artifact.plan.fingerprint()


def test_export_artifact_rejects_scene_mismatch(application: Application) -> None:
    from antcam_rc2.core.errors import ToolpathError

    project, scene = _scaffold(application, (OperationType.PROFILING,))
    from antcam_rc2.core.project.geometry_refs import invalidate_scene_index_cache

    scene.layer("profile", create=False).entities[0] = LineSegment(Point2(0.0, 0.0), Point2(1.0, 0.0))
    invalidate_scene_index_cache()  # contract: mutations invalidate the index cache explicitly

    with pytest.raises(ToolpathError, match="binding"):
        application.toolpath_service.export_artifact(project.id, scene, PlanningSettings(clearance_z_mm=5.0))


def test_validate_project_plan_inputs_reports_all_diagnostics(application: Application) -> None:
    project = application.project_service.create_project(
        "Fixture plate",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.FILLETTING,
        tool_id="ball_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "profile", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0, strategy_parameters={"radius_mm": 1.0}),
    )
    service = ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )

    diagnostics = service.validate_project_plan_inputs(project.id, scene, PlanningSettings(clearance_z_mm=5.0))

    assert any(d.code == "machine_capability_missing" for d in diagnostics)


def test_service_rejects_incompatible_tool_with_tool_incompatible_code(application: Application) -> None:
    project = application.project_service.create_project(
        "Fixture plate",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="drill_2",  # a drill cannot profile
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "profile", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0),
    )
    service = ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )

    plan = service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=5.0))

    assert plan.operations[0].status is OperationPlanStatus.FAILED
    assert plan.operations[0].diagnostics[0].code == "tool_incompatible"
    assert not plan.is_executable


def test_service_uses_wcs_offsets_for_absolute_z(application: Application) -> None:
    from antcam_rc2.core.project.models import WorkCoordinateSystem

    project = application.project_service.create_project(
        "Fixture plate",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    project = project.model_copy(update={"wcs": WorkCoordinateSystem(offset_z_mm=10.0)})
    application.project_service._projects.replace(project)
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.DRILL,
        tool_id="drill_2",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "holes", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0),
    )
    service = ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )

    plan = service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=5.0))
    program = plan.operations[0].program
    assert program is not None
    # Stock bottom 0 + offset 10 + height 5 = top 15; depth 2 → cut at 13.
    cuts = [m for m in program.motions if m.kind is MotionKind.CUT_LINEAR]
    assert cuts
    assert cuts[0].endpoint.z_mm == pytest.approx(13.0)


def test_plan_snapshot_plans_from_immutable_inputs(application: Application) -> None:
    project = application.project_service.create_project(
        "Fixture plate",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "profile", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0),
    )
    service = ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )
    snapshot = application.project_service.get_project(project.id)

    from_snapshot = service.plan_snapshot(snapshot, scene, PlanningSettings(clearance_z_mm=5.0))
    from_id = service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=5.0))

    assert from_snapshot.fingerprint() == from_id.fingerprint()
    assert from_snapshot.is_executable


def test_face_top_plans_with_allowance(application: Application) -> None:
    project = application.project_service.create_project(
        "Fixture plate",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.FACE_TOP,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "profile", 0),),
        operation_parameters=OperationParameters(depth_mm=None, stock_allowance_mm=0.5),
    )
    service = ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )

    plan = service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=5.0))
    assert plan.operations[0].status is OperationPlanStatus.SUCCEEDED
    assert plan.is_executable


def _scaffold(application: Application, operation_types: tuple[OperationType, ...]):
    """Create a project with one operation per requested type on the fixture scene."""
    project = application.project_service.create_project(
        "Fixture plate",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    for operation_type in operation_types:
        layer = "profile" if operation_type is OperationType.PROFILING else "holes"
        tool = "end_mill_3_175_2f" if operation_type is OperationType.PROFILING else "drill_2"
        application.project_service.add_operation(
            project.id,
            operation_type,
            tool_id=tool,
            cooling_id="aerodust",
            geometry_refs=(create_geometry_ref(scene, layer, 0),),
            operation_parameters=OperationParameters(depth_mm=2.0),
        )
    return project, scene


def test_service_generates_multiple_inward_loops_for_a_pocket(application: Application) -> None:
    project = application.project_service.create_project(
        "Fixture plate",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    operation = application.project_service.add_operation(
        project.id,
        OperationType.POCKETING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "profile", 0),),
        operation_parameters=OperationParameters(depth_mm=1.0, stepover_mm=2.0),
    )
    service = ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )

    plan = service.plan_project(project.id, scene, PlanningSettings(clearance_z_mm=5.0))

    result = plan.operations[0]
    assert result.status is OperationPlanStatus.SUCCEEDED
    assert result.program is not None
    assert sum(motion.kind is MotionKind.RAPID for motion in result.program.motions) >= 4
    assert operation.id == result.operation_id
