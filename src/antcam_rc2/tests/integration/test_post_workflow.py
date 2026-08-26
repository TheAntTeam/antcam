"""Integration tests: plan -> post -> .nc, determinism, and the CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from antcam_rc2.__main__ import main
from antcam_rc2.app.application import Application
from antcam_rc2.core.io import import_file
from antcam_rc2.core.post import PostService
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
from antcam_rc2.core.toolpath.settings import PlanningSettings

DATA = Path(__file__).parent.parent / "data"


def _prepare(application: Application, tmp_path: Path) -> tuple[Path, Path]:
    scene = import_file(DATA / "mini_polyline_bulge.dxf")
    project = application.project_service.create_project(
        "post workflow",
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
    project_path = tmp_path / "post.antcam.json"
    application.project_service.export_project(project.id, project_path)
    return project_path, DATA / "mini_polyline_bulge.dxf"


def test_post_workflow_generates_executable_nc(application: Application, tmp_path: Path) -> None:
    project_path, geometry = _prepare(application, tmp_path)
    fresh = Application()
    try:
        project = fresh.project_service.import_project(project_path)
        project = fresh.project_service.get_project(project.id)
        scene = import_file(geometry)
        plan = fresh.toolpath_service.plan_snapshot(project, scene, PlanningSettings(clearance_z_mm=5.0))
        program = PostService(fresh.catalog_repository).post(project, plan)
    finally:
        fresh.shutdown()

    assert program.post_id == "makera"  # default from native_post
    assert program.motion_line_count > 0
    text = program.text()
    assert text.startswith("; AntCAM RC2 — post makera")
    assert "M3 S" in text
    assert "G2" in text or "G3" in text  # the bulge fixture produces arcs
    assert text.rstrip().endswith("M2")


def test_post_export_nc_is_atomic_and_writable(application: Application, tmp_path: Path) -> None:
    project_path, geometry = _prepare(application, tmp_path)
    fresh = Application()
    try:
        project = fresh.project_service.import_project(project_path)
        project = fresh.project_service.get_project(project.id)
        scene = import_file(geometry)
        plan = fresh.toolpath_service.plan_snapshot(project, scene, PlanningSettings(clearance_z_mm=5.0))
        destination = tmp_path / "out.nc"
        service = PostService(fresh.catalog_repository)
        written = service.export_nc(project, plan, destination, post_id="grbl")
    finally:
        fresh.shutdown()
    assert written == destination
    content = destination.read_text(encoding="utf-8")
    assert "G90" in content and "M2" in content


def test_post_refuses_non_executable_plan(application: Application, tmp_path: Path) -> None:
    scene = import_file(DATA / "mini_polyline_bulge.dxf")
    project = application.project_service.create_project(
        "fail",
        machine_id="makera_z1",
        stock=Stock(width_mm=30, length_mm=30, height_mm=10, material_id="aluminum_6061"),
    )
    application.project_service.attach_geometry(project.id, scene)
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
    from antcam_rc2.core.errors import PostProcessorError

    with pytest.raises(PostProcessorError, match="not executable"):
        PostService(application.catalog_repository).post(project, plan)


def test_post_determinism_fingerprint_stable(application: Application) -> None:
    scene = import_file(DATA / "mini_polyline_bulge.dxf")
    project = application.project_service.create_project(
        "det", machine_id="makera_z1", stock=Stock(width_mm=30, length_mm=30, height_mm=10, material_id="aluminum_6061")
    )
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "0", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0),
    )
    project = application.project_service.get_project(project.id)
    plan = application.toolpath_service.plan_snapshot(project, scene, PlanningSettings(clearance_z_mm=5.0))
    service = PostService(application.catalog_repository)
    fingerprints = {service.post(project, plan, post_id="grbl").fingerprint() for _ in range(20)}
    assert len(fingerprints) == 1


def test_post_cli_writes_nc(tmp_path: Path) -> None:
    app = Application()
    try:
        project_path, geometry = _prepare(app, tmp_path)
    finally:
        app.shutdown()

    output = tmp_path / "cli.nc"
    exit_code = main(
        ["post", str(project_path), "--geometry", str(geometry), "--out", str(output), "--post", "grbl", "--dump"]
    )
    assert exit_code == 0
    content = output.read_text(encoding="utf-8")
    assert content.startswith("; AntCAM RC2 — post grbl")
    assert "M2" in content
