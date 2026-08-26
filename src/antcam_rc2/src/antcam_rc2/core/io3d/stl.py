"""STL importer: trimesh -> neutral :class:`SolidScene`."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from antcam_rc2.core.geometry3d.features import detect_features
from antcam_rc2.core.geometry3d.mesh import TriMesh
from antcam_rc2.core.geometry3d.scene import SolidBody, SolidScene, SolidSourceInfo

__all__ = ["import_stl"]


def import_stl(path: str | Path) -> SolidScene:
    """Import an STL mesh (single body or a scene of bodies)."""
    import trimesh

    file_path = Path(path)
    loaded = trimesh.load(str(file_path))
    geometries: list[trimesh.Trimesh] = []
    if isinstance(loaded, trimesh.Trimesh):
        geometries = [loaded]
    elif isinstance(loaded, trimesh.Scene):
        geometries = [geometry for geometry in loaded.geometry.values() if isinstance(geometry, trimesh.Trimesh)]
    else:
        geometries = []

    bodies: list[SolidBody] = []
    warnings: list[str] = []
    for index, geometry in enumerate(geometries):
        if len(geometry.vertices) == 0 or len(geometry.faces) == 0:
            warnings.append(f"body {index}: empty mesh skipped")
            continue
        if not geometry.is_watertight:
            warnings.append(f"body {index}: mesh is not watertight (features may be incomplete)")
        mesh = TriMesh(
            vertices=np.asarray(geometry.vertices, dtype=np.float64),
            faces=np.asarray(geometry.faces, dtype=np.int64),
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
        source=SolidSourceInfo(format="stl", path=str(file_path)),
        bodies=tuple(bodies),
        warnings=tuple(warnings),
        errors=() if bodies else (f"no solid geometry found in {file_path.name}",),
    )
