"""End-to-end tests for the headless Phase 4 ``antcam-rc2 plan`` command."""

from __future__ import annotations

import json
from pathlib import Path

from antcam_rc2.__main__ import main
from antcam_rc2.app.application import Application
from antcam_rc2.core.io import import_file
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock

DATA = Path(__file__).parent.parent / "data"


def test_plan_cli_writes_neutral_json_for_an_executable_project(tmp_path: Path) -> None:
    geometry_path = DATA / "mini_polyline_bulge.dxf"
    scene = import_file(geometry_path)
    app = Application()
    try:
        project = app.project_service.create_project(
            "Square profile",
            machine_id="makera_z1",
            stock=Stock(width_mm=20.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
        )
        app.project_service.attach_geometry(project.id, scene)
        app.project_service.add_operation(
            project.id,
            OperationType.PROFILING,
            tool_id="end_mill_3_175_2f",
            cooling_id="aerodust",
            geometry_refs=(create_geometry_ref(scene, "0", 0),),
            operation_parameters=OperationParameters(depth_mm=1.0),
        )
        project_path = tmp_path / "square.antcam.json"
        app.project_service.export_project(project.id, project_path)
    finally:
        app.shutdown()

    output_path = tmp_path / "square.toolpath.json"
    exit_code = main(
        [
            "plan",
            str(project_path),
            "--geometry",
            str(geometry_path),
            "--out",
            str(output_path),
            "--clearance-z",
            "3",
        ]
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["is_executable"] is True
    assert payload["operations"][0]["status"] == "succeeded"


def test_plan_cli_writes_versioned_artifact_when_requested(tmp_path: Path) -> None:
    geometry_path = DATA / "mini_polyline_bulge.dxf"
    scene = import_file(geometry_path)
    app = Application()
    try:
        project = app.project_service.create_project(
            "Square profile",
            machine_id="makera_z1",
            stock=Stock(width_mm=20.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
        )
        app.project_service.attach_geometry(project.id, scene)
        app.project_service.add_operation(
            project.id,
            OperationType.PROFILING,
            tool_id="end_mill_3_175_2f",
            cooling_id="aerodust",
            geometry_refs=(create_geometry_ref(scene, "0", 0),),
            operation_parameters=OperationParameters(depth_mm=1.0),
        )
        project_path = tmp_path / "square.antcam.json"
        app.project_service.export_project(project.id, project_path)
    finally:
        app.shutdown()

    output_path = tmp_path / "square.toolpath.json"
    artifact_path = tmp_path / "square.artifact.json"
    exit_code = main(
        [
            "plan",
            str(project_path),
            "--geometry",
            str(geometry_path),
            "--out",
            str(output_path),
            "--artifact",
            str(artifact_path),
        ]
    )

    assert exit_code == 0
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["artifact_schema_version"] == "1.0"
    assert artifact["scene_fingerprint"].startswith("sha256:")
    assert artifact["is_valid"] is True
    assert artifact["plan"]["is_executable"] is True
