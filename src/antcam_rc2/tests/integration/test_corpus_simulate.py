"""Corpus-driven simulation regression tests (parametrized from the manifest)."""

from __future__ import annotations

import pytest

from antcam_rc2.app.application import Application
from antcam_rc2.core.io import import_file
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
from antcam_rc2.core.simulation.models import SimulationSettings
from antcam_rc2.core.simulation.simulator import Simulator
from antcam_rc2.core.toolpath.settings import PlanningSettings
from tests.corpus.harness import iter_fixtures


def _simulate_fixtures() -> list:
    return [fixture for fixture in iter_fixtures() if fixture.expect.get("simulate") is not None]


@pytest.mark.parametrize("fixture", _simulate_fixtures(), ids=lambda fixture: fixture.name)
def test_simulate_removes_expected_volume(fixture, application: Application) -> None:
    simulate_expect = fixture.expect["simulate"]
    scene = import_file(fixture.absolute_path())
    scene.normalize()
    box = scene.bounding_box()
    plan_expect = fixture.expect["plan"]

    project = application.project_service.create_project(
        f"corpus sim {fixture.name}",
        machine_id="makera_z1",
        stock=Stock(
            width_mm=max(box.width, 1.0) + 10.0,
            length_mm=max(box.height, 1.0) + 10.0,
            height_mm=10.0,
            material_id="aluminum_6061",
        ),
    )
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, plan_expect["layer"], plan_expect["entity_index"]),),
        operation_parameters=OperationParameters(depth_mm=2.0),
    )
    project = application.project_service.get_project(project.id)
    plan = application.toolpath_service.plan_snapshot(project, scene, PlanningSettings(clearance_z_mm=5.0))
    assert plan.is_executable

    report = (
        Simulator(application.catalog_repository)
        .simulate(project, plan, SimulationSettings(voxel_resolution_mm=1.0))
        .report
    )
    assert report.stats.removed_mm3 >= simulate_expect["removed_mm3_min"]
