"""Unit tests for 3D feature references and the 2D converter."""

from __future__ import annotations

import numpy as np
import pytest

from antcam_rc2.core.errors import GeometryReferenceError
from antcam_rc2.core.geometry3d.convert import feature_plane_z_mm, feature_to_entities
from antcam_rc2.core.geometry3d.features import detect_features
from antcam_rc2.core.geometry3d.mesh import TriMesh
from antcam_rc2.core.geometry3d.scene import FeatureKind, SolidBody, SolidScene, SolidSourceInfo
from antcam_rc2.core.project.solid_refs import create_solid_ref, resolve_solid_ref


def _cube_scene() -> SolidScene:
    vertices = np.array(
        [
            [0, 0, 0],
            [1, 0, 0],
            [1, 1, 0],
            [0, 1, 0],
            [0, 0, 1],
            [1, 0, 1],
            [1, 1, 1],
            [0, 1, 1],
        ],
        dtype=np.float64,
    )
    faces = np.array(
        [
            [0, 2, 1],
            [0, 3, 2],
            [4, 5, 6],
            [4, 6, 7],
            [1, 2, 6],
            [1, 6, 5],
            [0, 4, 7],
            [0, 7, 3],
            [3, 7, 6],
            [3, 6, 2],
            [0, 1, 5],
            [0, 5, 4],
        ],
        dtype=np.int64,
    )
    mesh = TriMesh(vertices=vertices, faces=faces)
    body = SolidBody(id="b1", name="cube", mesh=mesh, features=detect_features(mesh))
    return SolidScene(source=SolidSourceInfo(format="stl"), bodies=(body,), tolerance_mm=0.01)


def test_create_and_resolve_solid_ref() -> None:
    scene = _cube_scene()
    reference = create_solid_ref(scene, 0, 0)
    feature = resolve_solid_ref(reference, scene)
    assert feature.feature_index == 0
    assert feature.fingerprint(scene.tolerance_mm) == reference.feature_fingerprint


def test_solid_ref_out_of_bounds() -> None:
    scene = _cube_scene()
    with pytest.raises(GeometryReferenceError):
        create_solid_ref(scene, 0, 99)
    with pytest.raises(GeometryReferenceError):
        create_solid_ref(scene, 99, 0)


def test_feature_to_entities_hole_and_face() -> None:
    scene = _cube_scene()
    perimeter = next(f for f in scene.bodies[0].features if f.kind is FeatureKind.PERIMETER)
    entities = feature_to_entities(perimeter)
    assert len(entities) == 1
    assert feature_plane_z_mm(perimeter) == pytest.approx(1.0)


def test_feature_to_entities_edge_cases() -> None:
    from antcam_rc2.core.geometry3d.scene import Feature3D

    # Degenerate hole (bypass validation via model_construct).
    hole = Feature3D.model_construct(kind=FeatureKind.HOLE, center=None, radius=None)
    assert feature_to_entities(hole) == ()

    # Too few boundary points.
    face = Feature3D(
        kind=FeatureKind.FACE_PLANAR, body_index=0, feature_index=0, boundary=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    )
    assert feature_to_entities(face) == ()

    # Closed loop with repeated last point is deduplicated.
    loop = Feature3D(
        kind=FeatureKind.FACE_PLANAR,
        body_index=0,
        feature_index=0,
        boundary=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 0.0, 0.0)),
    )
    assert len(feature_to_entities(loop)) == 1


def test_resolve_solid_ref_stale_and_missing() -> None:
    scene = _cube_scene()
    reference = create_solid_ref(scene, 0, 0)
    with pytest.raises(GeometryReferenceError):
        resolve_solid_ref(reference.model_copy(update={"body_index": 99}), scene)
    with pytest.raises(GeometryReferenceError):
        resolve_solid_ref(reference.model_copy(update={"feature_fingerprint": "sha256:" + "0" * 64}), scene)
