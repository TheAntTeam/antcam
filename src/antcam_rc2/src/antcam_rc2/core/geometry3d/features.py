"""Deterministic mesh feature detection for 3-axis machining.

Detects planar faces, circular holes and outer perimeters directly from a
:class:`TriMesh` using only numpy.  The algorithms favour simplicity and
speed over exact BRep topology: planar faces are normal clusters + connected
components, and holes/perimeters are boundary loops of Z-up faces fitted as
circles/contours.
"""

from __future__ import annotations

import math

import numpy as np

from antcam_rc2.core.geometry3d.mesh import TriMesh
from antcam_rc2.core.geometry3d.scene import Feature3D, FeatureKind

__all__ = ["detect_features"]

_Z_AXIS = np.array([0.0, 0.0, 1.0])


def detect_features(
    mesh: TriMesh,
    *,
    body_index: int = 0,
    angle_tol_deg: float = 15.0,
    min_area_mm2: float = 1.0,
    planar_tol_mm: float = 0.05,
    circularity_tol_mm: float = 0.1,
    facing_tol_deg: float = 25.0,
) -> tuple[Feature3D, ...]:
    """Detect machinable features from a mesh (deterministic order).

    Only Z-up faces (``facing``) produce ``HOLE``/``PERIMETER`` features, since
    those are the ones reachable by a 3-axis vertical spindle.  All planar
    faces are returned as ``FACE_PLANAR``.
    """
    if mesh.is_empty:
        return ()

    faces = mesh.faces
    normals = mesh.face_normals()
    areas = mesh.face_areas()
    adjacency = _edge_adjacency(faces)

    planar_groups = _planar_components(
        mesh.vertices,
        faces,
        normals,
        areas=areas,
        adjacency=adjacency,
        angle_tol_deg=angle_tol_deg,
        min_area_mm2=min_area_mm2,
        planar_tol_mm=planar_tol_mm,
    )

    features: list[Feature3D] = []
    next_index = 0
    # Deterministic order: largest planar group first.
    for face_indices in sorted(planar_groups, key=lambda indices: float(areas[indices].sum()), reverse=True):
        component_normals = normals[face_indices]
        normal = _average_normal(component_normals)
        outward = _outward_normal(mesh, faces, face_indices, normal, adjacency)
        facing = float(np.dot(outward, _Z_AXIS)) >= math.cos(math.radians(facing_tol_deg))
        if not facing:
            # Only Z-up faces are machinable by a 3-axis vertical spindle.
            continue
        plane_z = _plane_z(mesh, face_indices, outward)

        loops = _boundary_loops(faces[face_indices])
        loops = sorted(loops, key=_loop_area, reverse=True)
        for loop_index, loop in enumerate(loops):
            if loop_index == 0:
                features.append(
                    Feature3D(
                        kind=FeatureKind.PERIMETER,
                        body_index=body_index,
                        feature_index=next_index,
                        plane_z_mm=plane_z,
                        plane_normal=tuple(float(v) for v in outward),
                        boundary=_loop_points(mesh, loop, outward),
                        triangles=tuple(int(i) for i in face_indices),
                        facing=True,
                    )
                )
                next_index += 1
                continue
            center, radius, error = _fit_circle(mesh, loop)
            if error <= circularity_tol_mm:
                features.append(
                    Feature3D(
                        kind=FeatureKind.HOLE,
                        body_index=body_index,
                        feature_index=next_index,
                        plane_z_mm=plane_z,
                        plane_normal=tuple(float(v) for v in outward),
                        boundary=_loop_points(mesh, loop, outward),
                        triangles=tuple(int(i) for i in face_indices),
                        center=(float(center[0]), float(center[1])),
                        radius=float(radius),
                        facing=True,
                    )
                )
                next_index += 1

        features.append(
            Feature3D(
                kind=FeatureKind.FACE_PLANAR,
                body_index=body_index,
                feature_index=next_index,
                plane_z_mm=plane_z,
                plane_normal=tuple(float(v) for v in outward),
                boundary=_outer_boundary(mesh, faces[face_indices], outward),
                triangles=tuple(int(i) for i in face_indices),
                facing=True,
            )
        )
        next_index += 1

    return tuple(features)


def _planar_components(
    vertices: np.ndarray,
    faces: np.ndarray,
    normals: np.ndarray,
    *,
    areas: np.ndarray,
    adjacency: dict[tuple[int, int], list[int]],
    angle_tol_deg: float,
    min_area_mm2: float,
    planar_tol_mm: float,
) -> list[np.ndarray]:
    """Group coplanar, connected triangles into components."""
    angle_tol = math.radians(angle_tol_deg)
    bins = _normal_bins(normals, angle_tol)
    neighbors = _face_neighbors(adjacency)
    components: list[np.ndarray] = []
    for bin_faces in bins:
        for component in _connected_components(bin_faces, neighbors):
            if len(component) < 1:
                continue
            # Skip tiny/curved-surface clusters before the (costlier) planarity fit.
            if float(areas[component].sum()) < min_area_mm2:
                continue
            if _is_planar(vertices, faces, component, normals, planar_tol_mm):
                components.append(component)
    return components


def _normal_bins(normals: np.ndarray, angle_tol: float) -> list[np.ndarray]:
    """Bucket faces by coarse normal direction, then merge within tolerance."""
    keys: dict[tuple[int, int, int], list[int]] = {}
    for index, normal in enumerate(normals):
        key = (round(float(normal[0]) * 10.0), round(float(normal[1]) * 10.0), round(float(normal[2]) * 10.0))
        keys.setdefault(key, []).append(index)
    merged: list[np.ndarray] = []
    used = [False] * len(normals)
    for face_indices in keys.values():
        if not face_indices:
            continue
        representative = normals[face_indices[0]]
        group: list[int] = []
        for face in face_indices:
            if used[face]:
                continue
            if float(np.dot(representative, normals[face])) >= math.cos(angle_tol):
                group.append(face)
                used[face] = True
        if group:
            merged.append(np.asarray(group, dtype=np.int64))
    return merged


def _edge_adjacency(faces: np.ndarray) -> dict[tuple[int, int], list[int]]:
    """Map each undirected edge ``(min_vertex, max_vertex)`` to its faces."""
    adjacency: dict[tuple[int, int], list[int]] = {}
    for face_index, face in enumerate(faces):
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge = (int(min(a, b)), int(max(a, b)))
            adjacency.setdefault(edge, []).append(face_index)
    return adjacency


def _face_neighbors(adjacency: dict[tuple[int, int], list[int]]) -> dict[int, list[int]]:
    """Build ``face -> adjacent faces`` once (shared edges)."""
    neighbors: dict[int, list[int]] = {}
    for faces in adjacency.values():
        unique = sorted(set(faces))
        for index, face in enumerate(unique):
            for other in unique[index + 1 :]:
                neighbors.setdefault(face, []).append(other)
                neighbors.setdefault(other, []).append(face)
    return neighbors


def _connected_components(face_indices: np.ndarray, neighbors: dict[int, list[int]]) -> list[np.ndarray]:
    """Split face indices into components connected by shared edges."""
    allowed = set(int(i) for i in face_indices)
    seen: set[int] = set()
    components: list[np.ndarray] = []
    for face in sorted(allowed):
        if face in seen:
            continue
        stack = [face]
        seen.add(face)
        component: list[int] = []
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in neighbors.get(current, ()):
                if neighbor in allowed and neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(np.asarray(component, dtype=np.int64))
    return components


def _is_planar(
    vertices: np.ndarray, faces: np.ndarray, component: np.ndarray, normals: np.ndarray, tol_mm: float
) -> bool:
    """True when all component triangles lie within ``tol_mm`` of a plane."""
    if len(component) < 2:
        return True
    normal = _average_normal(normals[component])
    component_vertices = np.unique(faces[component].reshape(-1))
    points = vertices[component_vertices]
    distances = np.abs(points @ normal - float(np.dot(points, normal).mean()))
    return bool(float(distances.max()) <= tol_mm)


def _average_normal(normals: np.ndarray) -> np.ndarray:
    vector = normals.mean(axis=0)
    length = float(np.linalg.norm(vector))
    return vector / length if length > 1e-12 else _Z_AXIS.copy()


def _outward_normal(
    mesh: TriMesh,
    faces: np.ndarray,
    component: np.ndarray,
    winding_normal: np.ndarray,
    adjacency: dict[tuple[int, int], list[int]],
) -> np.ndarray:
    """Resolve the outward direction of a planar component.

    Triangle winding from tessellators is not guaranteed outward; instead we
    look at the triangles on the *other* side of the component's boundary
    edges.  Their centroids lie on the solid side, so the outward normal
    points away from them.
    """
    component_set = set(int(i) for i in component)
    neighbor_faces: list[int] = []
    for face_index in component:
        face = faces[face_index]
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge = (int(min(a, b)), int(max(a, b)))
            for other in adjacency.get(edge, ()):
                if other != int(face_index) and other not in component_set:
                    neighbor_faces.append(other)
    if not neighbor_faces:
        return winding_normal
    neighbor_centroids = mesh.vertices[faces[np.asarray(neighbor_faces, dtype=np.int64)]].mean(axis=1)
    plane_point = mesh.vertices[faces[component].reshape(-1)].mean(axis=0)
    side = float(np.mean((neighbor_centroids - plane_point) @ winding_normal))
    return -winding_normal if side > 0 else winding_normal


def _plane_z(mesh: TriMesh, face_indices: np.ndarray, normal: np.ndarray) -> float:
    vertices = mesh.vertices[mesh.faces[face_indices].reshape(-1)]
    return float(np.dot(vertices, normal).mean())


def _boundary_loops(faces: np.ndarray) -> list[np.ndarray]:
    """Return closed boundary loops as vertex-index arrays (CCW-agnostic)."""
    edge_count: dict[tuple[int, int], int] = {}
    for face in faces:
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge = (int(min(a, b)), int(max(a, b)))
            edge_count[edge] = edge_count.get(edge, 0) + 1
    boundary_edges = {edge for edge, count in edge_count.items() if count == 1}
    return _trace_loops(boundary_edges)


def _trace_loops(edges: set[tuple[int, int]]) -> list[np.ndarray]:
    """Trace boundary edges into closed loops.

    Boundary vertices have degree two, so each connected component of boundary
    edges is a single closed cycle; we walk each cycle once and rely on
    visited vertices (not edge removal) to avoid duplicate traversals.
    """
    adjacency: dict[int, list[int]] = {}
    for a, b in edges:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    for neighbors in adjacency.values():
        neighbors.sort()

    loops: list[np.ndarray] = []
    visited: set[int] = set()
    for start in sorted(adjacency):
        if start in visited:
            continue
        loop: list[int] = []
        previous = -1
        current = start
        while current not in visited:
            visited.add(current)
            loop.append(current)
            next_vertex = next(neighbor for neighbor in adjacency[current] if neighbor != previous)
            previous, current = current, next_vertex
        if len(loop) >= 3:
            loops.append(np.asarray(loop, dtype=np.int64))
    return loops


def _loop_points(mesh: TriMesh, loop: np.ndarray, normal: np.ndarray) -> tuple[tuple[float, float, float], ...]:
    """Project loop vertices onto the feature plane and drop collinear dupes."""
    points = mesh.vertices[loop]
    return tuple((float(p[0]), float(p[1]), float(p[2])) for p in points)


def _outer_boundary(mesh: TriMesh, faces: np.ndarray, normal: np.ndarray) -> tuple[tuple[float, float, float], ...]:
    loops = _boundary_loops(faces)
    if not loops:
        return ()
    loops = sorted(loops, key=_loop_area, reverse=True)
    return _loop_points(mesh, loops[0], normal)


def _loop_area(loop: np.ndarray) -> float:
    return float(len(loop))


def _fit_circle(mesh: TriMesh, loop: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Fit a circle to loop points on the XY plane; return center, radius, error."""
    points = mesh.vertices[loop]
    if len(points) < 6:
        center = points[:, :2].mean(axis=0)
        radius = float(np.linalg.norm(points[:, :2] - center, axis=1).mean()) if len(points) else 0.0
        error = float(np.linalg.norm(points[:, :2] - center, axis=1).max()) if len(points) else 0.0
        return center, radius, error
    x = points[:, 0]
    y = points[:, 1]
    design = np.column_stack([x, y, np.ones_like(x)])
    rhs = -(x * x + y * y)
    solution, *_ = np.linalg.lstsq(design, rhs, rcond=None)
    center = np.array([-solution[0] / 2.0, -solution[1] / 2.0])
    radius = float(math.sqrt(max(0.0, center[0] ** 2 + center[1] ** 2 - solution[2])))
    distances = np.linalg.norm(points[:, :2] - center, axis=1)
    error = float(np.abs(distances - radius).max())
    return center, radius, error
