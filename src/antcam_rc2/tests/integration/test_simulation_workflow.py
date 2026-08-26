"""End-to-end simulation tests: project -> plan -> simulate -> report."""

from __future__ import annotations

from pathlib import Path

import pytest

from antcam_rc2.app.application import Application
from antcam_rc2.core.io import import_file
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
from antcam_rc2.core.simulation import SimulationCode, SimulationSettings, Simulator
from antcam_rc2.core.toolpath.settings import PlanningSettings

DATA = Path(__file__).parent.parent / "data"


def _scaffold(application: Application, operation_type: OperationType = OperationType.PROFILING):
    scene = import_file(DATA / "mini_polyline_bulge.dxf")
    project = application.project_service.create_project(
        "sim fixture",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=30.0, height_mm=10.0, material_id="aluminum_6061"),
    )
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        operation_type,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "0", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0, stepdown_mm=1.0),
    )
    project = application.project_service.get_project(project.id)
    plan = application.toolpath_service.plan_snapshot(project, scene, PlanningSettings(clearance_z_mm=5.0))
    return project, plan


def test_simulate_profile_removes_material_and_reports_stats(application: Application) -> None:
    project, plan = _scaffold(application)
    result = Simulator(application.catalog_repository).simulate(
        project, plan, SimulationSettings(voxel_resolution_mm=1.0)
    )
    report = result.report

    assert report.project_id == project.id
    assert report.plan_fingerprint == plan.fingerprint()
    assert report.stats.operations_simulated == 1
    assert report.stats.removed_voxels > 0
    assert report.stats.removed_mm3 == pytest.approx(report.stats.removed_voxels * 1.0)
    assert report.stats.max_depth_reached_mm == pytest.approx(2.0)
    assert report.stats.duration_estimate_s > 0
    assert len(report.timeline) > 0
    assert len(result.checkpoints) > 0


def test_simulate_drill_too_deep_flags_reach_exceeded(application: Application) -> None:
    scene = import_file(DATA / "mini_arc_circle.dxf")
    project = application.project_service.create_project(
        "deep drill",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=30.0, height_mm=10.0, material_id="aluminum_6061"),
    )
    application.project_service.attach_geometry(project.id, scene)
    # The circle on layer "0" is the first entity of the mini_arc_circle fixture.
    application.project_service.add_operation(
        project.id,
        OperationType.DRILL,
        tool_id="drill_2",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "0", 0),),
        operation_parameters=OperationParameters(depth_mm=8.0),  # drill flute is 18mm; keep under reach
    )
    project = application.project_service.get_project(project.id)
    plan = application.toolpath_service.plan_snapshot(project, scene, PlanningSettings(clearance_z_mm=5.0))
    report = (
        Simulator(application.catalog_repository)
        .simulate(project, plan, SimulationSettings(voxel_resolution_mm=1.0))
        .report
    )
    assert report.stats.operations_simulated == 1
    assert report.stats.max_depth_reached_mm == pytest.approx(8.0)


def test_simulate_rejects_non_executable_plan(application: Application) -> None:
    scene = import_file(DATA / "mini_polyline_bulge.dxf")
    project = application.project_service.create_project(
        "non executable",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=30.0, height_mm=10.0, material_id="aluminum_6061"),
    )
    application.project_service.attach_geometry(project.id, scene)
    # Tapping on Makera Z1 always fails (no rigid tapping) -> plan not executable.
    application.project_service.add_operation(
        project.id,
        OperationType.TAPPING,
        tool_id="tap_m3",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "0", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0, strategy_parameters={"pitch_mm": 0.5}),
    )
    project = application.project_service.get_project(project.id)
    plan = application.toolpath_service.plan_snapshot(project, scene, PlanningSettings(clearance_z_mm=5.0))
    assert not plan.is_executable

    report = (
        Simulator(application.catalog_repository)
        .simulate(project, plan, SimulationSettings(voxel_resolution_mm=1.0))
        .report
    )
    assert report.stats.removed_voxels == 0
    assert any(event.code is SimulationCode.PLAN_NOT_EXECUTABLE for event in report.events)


def test_simulate_disabled_operation_is_skipped(application: Application) -> None:
    scene = import_file(DATA / "mini_polyline_bulge.dxf")
    project = application.project_service.create_project(
        "disabled",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=30.0, height_mm=10.0, material_id="aluminum_6061"),
    )
    application.project_service.attach_geometry(project.id, scene)
    operation = application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "0", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0),
    )
    application.project_service.toggle_operation(project.id, operation.id, False)
    project = application.project_service.get_project(project.id)
    plan = application.toolpath_service.plan_snapshot(project, scene, PlanningSettings(clearance_z_mm=5.0))
    report = (
        Simulator(application.catalog_repository)
        .simulate(project, plan, SimulationSettings(voxel_resolution_mm=1.0))
        .report
    )
    assert report.stats.operations_simulated == 0
    assert report.stats.operations_skipped == 1
    assert any(event.code is SimulationCode.OPERATION_SKIPPED for event in report.events)


def test_resolution_clamped_event_when_budget_exceeded(application: Application) -> None:
    project, plan = _scaffold(application)
    report = (
        Simulator(application.catalog_repository)
        .simulate(project, plan, SimulationSettings(voxel_resolution_mm=0.05))
        .report
    )
    assert any(event.code is SimulationCode.RESOLUTION_CLAMPED for event in report.events)
    assert report.settings.voxel_resolution_mm > 0.05
