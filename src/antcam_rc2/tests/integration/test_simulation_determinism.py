"""Determinism guarantees for the simulator: identical inputs -> identical reports."""

from __future__ import annotations

from pathlib import Path

from antcam_rc2.app.application import Application
from antcam_rc2.core.io import import_file
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
from antcam_rc2.core.simulation import SimulationReport, SimulationSettings, Simulator
from antcam_rc2.core.toolpath.settings import PlanningSettings

DATA = Path(__file__).parent.parent / "data"


def _build(application: Application):
    scene = import_file(DATA / "mini_polyline_bulge.dxf")
    project = application.project_service.create_project(
        "determinism",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=30.0, height_mm=10.0, material_id="aluminum_6061"),
    )
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "0", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0, stepdown_mm=1.0),
    )
    project = application.project_service.get_project(project.id)
    plan = application.toolpath_service.plan_snapshot(project, scene, PlanningSettings(clearance_z_mm=5.0))
    return project, plan


def test_simulation_fingerprint_is_stable_over_repeated_runs(application: Application) -> None:
    project, plan = _build(application)
    simulator = Simulator(application.catalog_repository)
    fingerprints = {
        simulator.simulate(project, plan, SimulationSettings(voxel_resolution_mm=1.0)).report.fingerprint()
        for _ in range(20)
    }
    assert len(fingerprints) == 1


def test_simulation_report_json_round_trip_preserves_fingerprint(application: Application) -> None:
    project, plan = _build(application)
    report = (
        Simulator(application.catalog_repository)
        .simulate(project, plan, SimulationSettings(voxel_resolution_mm=1.0))
        .report
    )
    restored = SimulationReport.model_validate_json(report.model_dump_json())
    assert restored.fingerprint() == report.fingerprint()


def test_different_resolution_changes_the_report(application: Application) -> None:
    project, plan = _build(application)
    simulator = Simulator(application.catalog_repository)
    coarse = simulator.simulate(project, plan, SimulationSettings(voxel_resolution_mm=1.0)).report
    fine = simulator.simulate(project, plan, SimulationSettings(voxel_resolution_mm=0.5)).report
    assert coarse.fingerprint() != fine.fingerprint()
