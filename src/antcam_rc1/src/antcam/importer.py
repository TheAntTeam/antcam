from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import trimesh
from OCP.BRep import BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.ShapeFix import ShapeFix_ShapeTolerance
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape

from antcam.logger import setup_logger

log = setup_logger()


@dataclass
class Model:
    shape: TopoDS_Shape
    source_path: Path
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    _mesh: Optional[trimesh.Trimesh] = field(default=None, repr=False)

    @classmethod
    def from_step(cls, path: str | Path, rx: float = 0, ry: float = 0, rz: float = 0) -> Model:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        Interface_Static.SetIVal_s("read.stepcaf.subshape", 0)
        reader = STEPControl_Reader()
        status = reader.ReadFile(str(path))

        if status != IFSelect_RetDone:
            raise RuntimeError(f"STEP import failed for: {path} (status={status})")

        reader.TransferRoots()
        shape = reader.OneShape()

        if shape.IsNull():
            raise RuntimeError(f"STEP import returned null shape: {path}")

        analyzer = BRepCheck_Analyzer(shape)
        if not analyzer.IsValid():
            log.warning(f"Shape validity check produced warnings for {path.name}")

        if rx or ry or rz:
            shape = cls._apply_rotation(shape, rx, ry, rz)

        log.info(f"Loaded STEP: {path.name} | valid={analyzer.IsValid()}")
        return cls(shape=shape, source_path=path, rotation=(rx, ry, rz))

    @classmethod
    def from_stl(cls, path: str | Path, rx: float = 0, ry: float = 0, rz: float = 0) -> Model:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        mesh = trimesh.load_mesh(str(path))
        if mesh.is_empty:
            raise RuntimeError(f"STL import returned empty mesh: {path}")

        if rx or ry or rz:
            rad = np.radians([rx, ry, rz])
            R = trimesh.transformations.euler_matrix(*rad)
            mesh.apply_transform(R)

        log.info(f"Loaded STL: {path.name} | faces={len(mesh.faces)} verts={len(mesh.vertices)}")
        return cls(shape=TopoDS_Shape(), source_path=path, rotation=(rx, ry, rz), _mesh=mesh)

    @classmethod
    def _apply_rotation(cls, shape: TopoDS_Shape, rx: float, ry: float, rz: float) -> TopoDS_Shape:
        from OCP.gp import gp_Ax1, gp_Dir, gp_Trsf
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

        rad = [math.radians(rx), math.radians(ry), math.radians(rz)]
        from OCP.gp import gp_Pnt

        origin = gp_Pnt(0, 0, 0)

        trsf = gp_Trsf()
        if rad[0]:
            trsf.SetRotation(gp_Ax1(origin, gp_Dir(1, 0, 0)), rad[0])
        if rad[1]:
            trsf.SetRotation(gp_Ax1(origin, gp_Dir(0, 1, 0)), rad[1])
        if rad[2]:
            trsf.SetRotation(gp_Ax1(origin, gp_Dir(0, 0, 1)), rad[2])

        return BRepBuilderAPI_Transform(shape, trsf).Shape()

    @property
    def is_mesh_only(self) -> bool:
        return self._mesh is not None

    @property
    def mesh(self) -> trimesh.Trimesh:
        if self._mesh is not None:
            return self._mesh
        return self._tessellate_brep()

    def _tessellate_brep(self, tolerance: float = 0.1, angle_tolerance: float = 0.5) -> trimesh.Trimesh:
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.StlAPI import StlAPI_Writer
        import io
        import tempfile

        mesh = BRepMesh_IncrementalMesh(self.shape, tolerance, True, angle_tolerance)
        mesh.Perform()
        if not mesh.IsDone():
            raise RuntimeError("BRep tessellation failed")

        with tempfile.NamedTemporaryFile(suffix=".stl", delete=True) as tmp:
            writer = StlAPI_Writer()
            writer.Write(self.shape, tmp.name)
            tmp.seek(0)
            result = trimesh.load_mesh(tmp.name, file_type="stl")

        self._mesh = result
        return result

    def bounding_box(self) -> tuple[float, float, float]:
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(self.shape, props)
        center = props.CentreOfMass()
        return (center.X(), center.Y(), center.Z())

    def face_count(self) -> int:
        exp = TopExp_Explorer(self.shape, TopAbs_FACE)
        count = 0
        while exp.More():
            count += 1
            exp.Next()
        return count

    def to_dict(self) -> dict:
        return {
            "source": str(self.source_path),
            "rotation": list(self.rotation),
            "mesh_only": self.is_mesh_only,
            "face_count": self.face_count(),
            "bounds": list(self.bounding_box()),
        }
