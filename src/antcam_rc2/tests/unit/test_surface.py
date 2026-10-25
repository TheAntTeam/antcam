"""Tests for the smooth dual (surface-nets) display mesh."""

from __future__ import annotations

import numpy as np

from antcam_rc2.core.simulation.surface import greedy_surface_mesh, smooth_surface_mesh


def test_smooth_surface_mesh_is_a_closed_rounded_box() -> None:
    occ = np.ones((4, 4, 4), dtype=bool)
    vertices, triangles = smooth_surface_mesh(occ, (0.0, 0.0, 0.0), 1.0)
    assert vertices.shape[1] == 3 and triangles.shape[1] == 3
    assert len(triangles) > 0
    assert int(triangles.min()) >= 0 and int(triangles.max()) < len(vertices)
    # Vertices stay inside the (padded) block bounds.
    assert float(vertices.min()) >= 0.0
    assert float(vertices.max()) <= 4.0


def test_smooth_surface_mesh_carves_a_pocket() -> None:
    occ = np.ones((10, 10, 10), dtype=bool)
    pocket = occ.copy()
    pocket[3:7, 3:7, 5:8] = False
    solid_vertices, _ = smooth_surface_mesh(occ, (0.0, 0.0, 0.0), 1.0)
    carved_vertices, carved_triangles = smooth_surface_mesh(pocket, (0.0, 0.0, 0.0), 1.0)
    # The pocket adds surface, so the carved mesh has more vertices.
    assert len(carved_triangles) > 0
    assert len(carved_vertices) > len(solid_vertices)


def test_greedy_surface_mesh_merges_a_full_block_into_six_quads() -> None:
    vertices, triangles = greedy_surface_mesh(np.ones((4, 4, 4), dtype=bool), (0.0, 0.0, 0.0), 1.0)
    # 6 faces x 2 triangles: the flat block collapses into 12 triangles.
    assert len(triangles) == 12
    corners = vertices[triangles]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    assert np.all(lengths > 1e-9)  # no degenerate faces
    rounded = {(round(float(n[0])), round(float(n[1])), round(float(n[2]))) for n in normals / lengths[:, None]}
    assert rounded == {
        (1.0, 0.0, 0.0),
        (-1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, -1.0, 0.0),
        (0.0, 0.0, 1.0),
        (0.0, 0.0, -1.0),
    }


def test_greedy_surface_mesh_stays_tiny_on_a_large_flat_grid() -> None:
    occ = np.ones((200, 200, 10), dtype=bool)
    occ[50:150, 50:150, 5:10] = False
    vertices, triangles = greedy_surface_mesh(occ, (0.0, 0.0, 0.0), 1.0)
    assert int(triangles.min()) >= 0 and int(triangles.max()) < len(vertices)
    # The uniform per-face mesh needs ~180k triangles; the adaptive one a few dozen.
    assert len(triangles) < 200
    assert float(vertices.min()) >= 0.0
    assert float(vertices.max()) <= 200.0


def test_smooth_surface_mesh_empty_grid() -> None:
    vertices, triangles = smooth_surface_mesh(np.zeros((3, 3, 3), dtype=bool), (0.0, 0.0, 0.0), 1.0)
    assert vertices.shape == (0, 3)
    assert triangles.shape == (0, 3)
