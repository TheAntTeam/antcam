"""STEP importer: OCP tessellation -> neutral :class:`SolidScene`.

OCP is imported lazily and only here; the rest of the pipeline never depends
on it.  If the ``3d`` extra is not installed, a clear ``ConfigurationError``
is raised while STL import keeps working.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from antcam_rc2.core.errors import ConfigurationError, GeometryError
from antcam_rc2.core.geometry3d.features import detect_features
from antcam_rc2.core.geometry3d.mesh import TriMesh
from antcam_rc2.core.geometry3d.scene import SolidBody, SolidScene, SolidSourceInfo

__all__ = ["import_step"]


def import_step(path: str | Path, *, tolerance_mm: float = 0.05, angular_deflection_deg: float = 0.5) -> SolidScene:
    """Import a STEP solid by tessellating it into triangle meshes."""
    try:
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_Reader
        from OCP.TopAbs import TopAbs_SOLID
    except ImportError as exc:
        raise ConfigurationError(
            "STEP import requires the optional '3d' extra; install with: pip install -e 'src/antcam_rc2[3d]'"
        ) from exc

    file_path = Path(path)
    reader = STEPControl_Reader()
    if reader.ReadFile(str(file_path)) != IFSelect_RetDone:
        raise GeometryError(f"failed to read STEP file: {file_path.name}")
    reader.TransferRoots()
    shape = reader.OneShape()

    bodies: list[SolidBody] = []
    warnings: list[str] = []

    solids = _collect_subshapes(shape, TopAbs_SOLID)
    if not solids:
        solids = [shape]

    for body_index, solid in enumerate(solids):
        BRepMesh_IncrementalMesh(
            solid, tolerance_mm, False, angular_deflection_deg * 0.017453292519943295, True
        ).Perform()
        vertices, faces = _tessellate_solid(solid)
        if not faces:
            warnings.append(f"body {body_index}: no triangles produced")
            continue
        mesh = TriMesh(
            vertices=np.asarray(vertices, dtype=np.float64),
            faces=np.asarray(faces, dtype=np.int64),
        )
        features = detect_features(mesh, body_index=len(bodies))
        bodies.append(
            SolidBody(
                id=f"body_{len(bodies)}",
                name=f"{file_path.stem}_{len(bodies)}",
                mesh=mesh,
                features=features,
            )
        )

    return SolidScene(
        source=SolidSourceInfo(format="step", path=str(file_path)),
        bodies=tuple(bodies),
        warnings=tuple(warnings),
        errors=() if bodies else (f"no solid geometry found in {file_path.name}",),
    )


def _collect_subshapes(shape, kind) -> list:
    """Return subshapes of ``shape`` of the requested TopAbs kind."""
    from OCP.TopExp import TopExp_Explorer

    explorer = TopExp_Explorer(shape, kind)
    shapes: list = []
    while explorer.More():
        shapes.append(explorer.Current())
        explorer.Next()
    return shapes


def _tessellate_solid(solid) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    """Iterate faces of a TopoDS solid and collect indexed triangles."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location

    vertex_map: dict[tuple[int, int, int], int] = {}
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    def add_vertex(point) -> int:
        x, y, z = point.X(), point.Y(), point.Z()
        key = (round(x, 6), round(y, 6), round(z, 6))
        index = vertex_map.get(key)
        if index is None:
            index = len(vertices)
            vertex_map[key] = index
            vertices.append((x, y, z))
        return index

    from OCP.TopoDS import TopoDS

    explorer = TopExp_Explorer(solid, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is not None:
            transformation = location.Transformation()
            node_indices: dict[int, int] = {}
            for node in range(1, triangulation.NbNodes() + 1):
                point = triangulation.Node(node).Transformed(transformation)
                node_indices[node] = add_vertex(point)
            for triangle in range(1, triangulation.NbTriangles() + 1):
                entry = triangulation.Triangle(triangle)
                a, b, c = entry.Value(1), entry.Value(2), entry.Value(3)
                faces.append((node_indices[a], node_indices[b], node_indices[c]))
        explorer.Next()

    return vertices, faces
