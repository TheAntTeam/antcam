"""Golden determinism: plan fingerprint + simulation stats for a fixed subset.

The golden files in ``tests/corpus/expected/`` pin the exact plan fingerprint
and simulation stats for three representative fixtures.  Any numerical change
that alters a fingerprint (even while semantic invariants hold) must be
reviewed here — this is the explicit determinism contract of the corpus.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from antcam_rc2.app.application import Application
from antcam_rc2.core.io import import_file
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
from antcam_rc2.core.simulation.models import SimulationSettings
from antcam_rc2.core.simulation.simulator import Simulator
from antcam_rc2.core.toolpath.settings import PlanningSettings
from tests.corpus.harness import CORPUS_DIR, iter_fixtures

EXPECTED_DIR = CORPUS_DIR / "expected"

_SKIP_KEYS = {"project_id", "operation_id"}


def canonical_fingerprint(obj) -> str:
    """SHA-256 over the dump with project-scoped UUIDs removed.

    Project ids are random per ``create_project`` call; the golden pins the
    project-INDEPENDENT content (geometry, settings, catalog versions).
    """

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in _SKIP_KEYS}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    encoded = json.dumps(clean(obj), ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def simulation_canonical_fingerprint(report, plan) -> str:
    """Canonical report fingerprint with the embedded plan fingerprint swapped."""
    payload = report.model_dump(mode="json")
    payload["plan_fingerprint"] = canonical_fingerprint(plan.model_dump(mode="json"))
    return canonical_fingerprint(payload)


_GOLDEN_NAMES = ["mech_plate", "pcb_panel", "mini_polyline_bulge"]


def _golden_fixtures() -> list:
    return [fixture for fixture in iter_fixtures() if fixture.name in _GOLDEN_NAMES]


def _build_and_run(fixture, application: Application):
    plan_expect = fixture.expect["plan"]
    scene = import_file(fixture.absolute_path())
    scene.normalize()
    box = scene.bounding_box()
    project = application.project_service.create_project(
        f"golden {fixture.name}",
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
    report = (
        Simulator(application.catalog_repository)
        .simulate(project, plan, SimulationSettings(voxel_resolution_mm=1.0))
        .report
    )
    return {
        "name": fixture.name,
        "plan_canonical_fingerprint": canonical_fingerprint(plan.model_dump(mode="json")),
        "operation_count": len(plan.operations),
        "executable": plan.is_executable,
        "removed_voxels": report.stats.removed_voxels,
        "removed_mm3": round(report.stats.removed_mm3, 6),
        "operations_simulated": report.stats.operations_simulated,
        "simulation_canonical_fingerprint": simulation_canonical_fingerprint(report, plan),
    }


@pytest.mark.parametrize("fixture", _golden_fixtures(), ids=lambda fixture: fixture.name)
def test_golden_plan_and_simulation(fixture, application: Application) -> None:
    golden_path = EXPECTED_DIR / f"{fixture.name}.json"
    assert golden_path.exists(), f"missing golden file {golden_path.name}"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))

    actual = _build_and_run(fixture, application)
    assert actual == golden, f"golden mismatch for {fixture.name}: {actual}"


def test_golden_files_are_committed_and_well_formed() -> None:
    for name in _GOLDEN_NAMES:
        path = EXPECTED_DIR / f"{name}.json"
        assert path.is_file(), f"golden {path.name} missing"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["plan_canonical_fingerprint"].startswith("sha256:")
        assert payload["executable"] is True
        assert payload["removed_voxels"] > 0
        assert payload["simulation_canonical_fingerprint"].startswith("sha256:")
