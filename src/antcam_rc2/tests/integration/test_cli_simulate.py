"""End-to-end tests for the headless ``antcam-rc2 simulate`` command."""

from __future__ import annotations

import json
from pathlib import Path

from antcam_rc2.__main__ import main
from antcam_rc2.app.application import Application
from antcam_rc2.core.io import import_file
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock

DATA = Path(__file__).parent.parent / "data"


def _write_executable_project(tmp_path: Path, app: Application) -> Path:
    geometry_path = DATA / "mini_polyline_bulge.dxf"
    scene = import_file(geometry_path)
    project = app.project_service.create_project(
        "Sim CLI",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=30.0, height_mm=10.0, material_id="aluminum_6061"),
    )
    app.project_service.attach_geometry(project.id, scene)
    app.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "0", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0),
    )
    project_path = tmp_path / "sim.antcam.json"
    app.project_service.export_project(app.project_service.get_project(project.id).id, project_path)
    return project_path


def test_simulate_cli_writes_report_json(tmp_path: Path) -> None:
    app = Application()
    try:
        project_path = _write_executable_project(tmp_path, app)
    finally:
        app.shutdown()

    output_path = tmp_path / "report.simulation.json"
    exit_code = main(
        [
            "simulate",
            str(project_path),
            "--geometry",
            str(DATA / "mini_polyline_bulge.dxf"),
            "--out",
            str(output_path),
            "--resolution",
            "1.0",
        ]
    )

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["stats"]["removed_voxels"] > 0
    assert payload["stats"]["operations_simulated"] == 1


def test_simulate_cli_rejects_non_executable_plan(tmp_path: Path) -> None:
    app = Application()
    try:
        geometry_path = DATA / "mini_polyline_bulge.dxf"
        scene = import_file(geometry_path)
        project = app.project_service.create_project(
            "Sim CLI fail",
            machine_id="makera_z1",
            stock=Stock(width_mm=30.0, length_mm=30.0, height_mm=10.0, material_id="aluminum_6061"),
        )
        app.project_service.attach_geometry(project.id, scene)
        app.project_service.add_operation(
            project.id,
            OperationType.TAPPING,
            tool_id="tap_m3",
            cooling_id="aerodust",
            geometry_refs=(create_geometry_ref(scene, "0", 0),),
            operation_parameters=OperationParameters(depth_mm=2.0, strategy_parameters={"pitch_mm": 0.5}),
        )
        project_path = tmp_path / "fail.antcam.json"
        app.project_service.export_project(app.project_service.get_project(project.id).id, project_path)
    finally:
        app.shutdown()

    output_path = tmp_path / "fail.simulation.json"
    exit_code = main(
        [
            "simulate",
            str(project_path),
            "--geometry",
            str(geometry_path),
            "--out",
            str(output_path),
        ]
    )
    assert exit_code == 1
    assert not output_path.exists()
