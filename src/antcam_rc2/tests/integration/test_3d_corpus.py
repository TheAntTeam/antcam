"""Corpus regression tests for the 3D importers (real STEP/STL parts).

The bundled parts live in ``tests/data/`` and exercise real-world geometry:
curved surfaces, fillets, holes and multi-body STL tessellations.  Importing
STEP files tessellates via OCP, so those cases are slower than STL.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from antcam_rc2.core.geometry3d.scene import FeatureKind
from antcam_rc2.core.io3d import import_file_3d

DATA_DIR = Path(__file__).parent.parent / "data"

_CORPUS = (
    ("bottle_opener.step", True),
    ("flange.step", True),
    ("mounting_spider.step", True),
    ("MALE_BUCKLE.stl", False),
    ("servo_mount.stl", False),
)


@pytest.fixture(scope="module")
def corpus_scenes() -> dict[str, object]:
    """Import every corpus file once and share across the module's tests."""
    scenes: dict[str, object] = {}
    for filename, _step in _CORPUS:
        scenes[filename] = import_file_3d(str(DATA_DIR / filename))
    return scenes


def _scene(filename: str, corpus_scenes) -> object:
    return corpus_scenes[filename]


@pytest.mark.parametrize("filename,_step", _CORPUS)
def test_import_corpus_produces_solid_scene(filename: str, _step: bool, corpus_scenes) -> None:
    scene = _scene(filename, corpus_scenes)
    assert not scene.has_errors()
    assert len(scene.bodies) >= 1
    assert scene.bodies[0].mesh.face_count > 0
    assert scene.feature_count() >= 1


@pytest.mark.parametrize("filename", ["MALE_BUCKLE.stl", "servo_mount.stl"])
def test_import_corpus_is_deterministic(filename: str) -> None:
    first = import_file_3d(str(DATA_DIR / filename))
    second = import_file_3d(str(DATA_DIR / filename))
    assert first.fingerprint() == second.fingerprint()


@pytest.mark.parametrize("filename,_step", [c for c in _CORPUS if c[1]])
def test_step_parts_detect_holes(filename: str, _step: bool, corpus_scenes) -> None:
    scene = _scene(filename, corpus_scenes)
    kinds = {feature.kind for feature in scene.bodies[0].features}
    assert FeatureKind.HOLE in kinds


def test_stl_parts_detect_perimeters(corpus_scenes) -> None:
    for filename in ("MALE_BUCKLE.stl", "servo_mount.stl"):
        scene = _scene(filename, corpus_scenes)
        kinds = {feature.kind for feature in scene.bodies[0].features}
        assert FeatureKind.PERIMETER in kinds


def test_plan3d_on_corpus_stl(application) -> None:
    """End-to-end 3-axis planning on a fast STL corpus part."""
    from antcam_rc2.core.geometry3d.scene import FeatureKind
    from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
    from antcam_rc2.core.io3d import import_file_3d
    from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
    from antcam_rc2.core.project.solid_refs import create_solid_ref
    from antcam_rc2.core.toolpath.settings import PlanningSettings

    project = application.project_service.create_project(
        "3d corpus",
        machine_id="makera_z1",
        stock=Stock(width_mm=60, length_mm=60, height_mm=20, material_id="aluminum_6061"),
    )
    solid = import_file_3d(str(DATA_DIR / "MALE_BUCKLE.stl"))
    face = next(
        (body_index, feature_index)
        for body_index, body in enumerate(solid.bodies)
        for feature_index, feature in enumerate(body.features)
        if feature.kind is FeatureKind.FACE_PLANAR and feature.facing
    )
    reference = create_solid_ref(solid, face[0], face[1])
    application.project_service.add_operation(
        project.id,
        OperationType.POCKETING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        solid_refs=(reference,),
        operation_parameters=OperationParameters(depth_mm=1.0),
    )
    project = application.project_service.get_project(project.id)
    empty = GeometryScene(source=SourceInfo(format="dxf"))
    plan = application.toolpath_service.plan_snapshot(
        project, empty, PlanningSettings(clearance_z_mm=5.0), solid_scene=solid
    )
    assert plan.is_executable
    assert plan.operations[0].status.value == "succeeded"
