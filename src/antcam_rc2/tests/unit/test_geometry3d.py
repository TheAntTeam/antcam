"""Unit tests for the pure 3D mesh model and feature detection."""

from __future__ import annotations

import numpy as np
import pytest

from antcam_rc2.core.geometry3d.features import detect_features
from antcam_rc2.core.geometry3d.mesh import TriMesh
from antcam_rc2.core.geometry3d.picking import ray_mesh_hit
from antcam_rc2.core.geometry3d.scene import FeatureKind, SolidBody, SolidScene, SolidSourceInfo


def _cube_mesh() -> TriMesh:
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
            [0, 3, 2],  # bottom
            [4, 5, 6],
            [4, 6, 7],  # top
            [1, 2, 6],
            [1, 6, 5],  # +X
            [0, 4, 7],
            [0, 7, 3],  # -X
            [3, 7, 6],
            [3, 6, 2],  # +Y
            [0, 1, 5],
            [0, 5, 4],  # -Y
        ],
        dtype=np.int64,
    )
    return TriMesh(vertices=vertices, faces=faces)


def test_mesh_normals_areas_and_bounds() -> None:
    mesh = _cube_mesh()
    assert mesh.vertex_count == 8
    assert mesh.face_count == 12
    assert (mesh.face_areas() == 0.5).all()
    min_corner, max_corner = mesh.bounds()
    np.testing.assert_allclose(min_corner, [0, 0, 0])
    np.testing.assert_allclose(max_corner, [1, 1, 1])


def test_mesh_rejects_bad_indices() -> None:
    with pytest.raises(ValueError, match="out-of-range"):
        TriMesh(vertices=np.zeros((2, 3)), faces=np.array([[0, 1, 2]], dtype=np.int64))


def test_mesh_validation_branches() -> None:
    with pytest.raises(ValueError, match="vertices"):
        TriMesh(vertices=np.zeros((2, 2)), faces=np.zeros((1, 3), dtype=np.int64))
    with pytest.raises(ValueError, match="faces"):
        TriMesh(vertices=np.zeros((3, 3)), faces=np.zeros((1, 2), dtype=np.int64))
    with pytest.raises(ValueError, match="integer"):
        TriMesh(vertices=np.zeros((3, 3)), faces=np.zeros((1, 3), dtype=np.float64))
    with pytest.raises(ValueError, match="face_ids"):
        TriMesh(
            vertices=np.zeros((3, 3)),
            faces=np.zeros((1, 3), dtype=np.int64),
            face_ids=np.zeros((2,), dtype=np.int32),
        )


def test_mesh_empty_and_canonical_dtype() -> None:
    empty = TriMesh(vertices=np.empty((0, 3)), faces=np.empty((0, 3), dtype=np.int64))
    assert empty.is_empty
    assert empty.face_normals().shape == (0, 3)
    assert empty.face_areas().shape == (0,)
    np.testing.assert_allclose(empty.bounds()[0], [0, 0, 0])
    canonical = _cube_mesh().as_float64()
    assert canonical.vertices.dtype == np.float64
    assert canonical.faces.dtype == np.int64


def test_detect_cube_top_and_bottom_facing() -> None:
    features = detect_features(_cube_mesh())
    kinds = [feature.kind for feature in features]
    assert FeatureKind.PERIMETER in kinds
    # Only the Z-up top face is emitted (3-axis machinable); side/bottom faces are skipped.
    assert kinds.count(FeatureKind.FACE_PLANAR) == 1
    top = [
        feature
        for feature in features
        if feature.plane_normal == (0.0, 0.0, 1.0) and feature.kind is FeatureKind.FACE_PLANAR
    ]
    assert len(top) == 1
    assert top[0].facing is True
    assert top[0].plane_z_mm == pytest.approx(1.0)


def test_detect_ring_face_finds_hole_and_perimeter() -> None:
    # A planar annulus (ring) on the XY plane: outer radius 10, inner radius 5.
    segments = 32
    angles = np.linspace(0.0, 2.0 * np.pi, segments, endpoint=False)
    outer = np.column_stack([10.0 * np.cos(angles), 10.0 * np.sin(angles), np.zeros(segments)])
    inner = np.column_stack([5.0 * np.cos(angles), 5.0 * np.sin(angles), np.zeros(segments)])
    vertices = np.vstack([outer, inner])
    faces = []
    for index in range(segments):
        next_index = (index + 1) % segments
        o0, o1 = index, next_index
        i0, i1 = segments + index, segments + next_index
        faces.append([o0, o1, i1])
        faces.append([o0, i1, i0])
    mesh = TriMesh(vertices=np.asarray(vertices, dtype=np.float64), faces=np.asarray(faces, dtype=np.int64))

    features = detect_features(mesh, planar_tol_mm=0.01, circularity_tol_mm=0.2)
    holes = [feature for feature in features if feature.kind is FeatureKind.HOLE]
    perimeters = [feature for feature in features if feature.kind is FeatureKind.PERIMETER]
    assert len(holes) == 1
    assert holes[0].radius == pytest.approx(5.0, abs=0.15)
    assert len(perimeters) == 1


def test_scene_fingerprint_stable() -> None:
    mesh = _cube_mesh()
    body = SolidBody(id="b1", name="cube", mesh=mesh, features=detect_features(mesh))
    scene = SolidScene(source=SolidSourceInfo(format="stl"), bodies=(body,), tolerance_mm=0.01)
    assert scene.fingerprint() == scene.fingerprint()
    assert scene.feature_count() == len(body.features)


def test_ray_mesh_hit_top_and_miss() -> None:
    mesh = _cube_mesh()
    body = SolidBody(id="b1", name="cube", mesh=mesh, features=detect_features(mesh))
    scene = SolidScene(source=SolidSourceInfo(format="stl"), bodies=(body,), tolerance_mm=0.01)
    hit = ray_mesh_hit(scene, (0.5, 0.5, 2.0), (0.0, 0.0, -1.0))
    assert hit is not None
    assert hit[0] == 0
    assert hit[1] >= 0
    assert ray_mesh_hit(scene, (5.0, 5.0, 2.0), (0.0, 0.0, -1.0)) is None
