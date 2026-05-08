from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone, IFSelect_ItemsByEntity
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform, BRepBuilderAPI_Sewing, BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
from OCP.gp import gp_Trsf, gp_Ax1, gp_Pnt, gp_Dir, gp_EulerSequence, gp_Ax2
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.BRepLib import BRepLib
import math
import trimesh
import trimesh.repair
import numpy as np
from antcam.logger import setup_logger

"""Model import utilities for STEP and STL sources.

The current production strategy is:
- STEP/STP -> native BRep workflow
- STL -> mesh-first workflow (BRep conversion disabled by default in Model)

Mesh-to-BRep helpers are kept for experimentation and debug workflows but are
not part of the default STL pipeline.
"""

logger = setup_logger()

def apply_rotation(shape, rx: float = 0.0, ry: float = 0.0, rz: float = 0.0):
    """Apply XYZ Euler-like incremental rotations (degrees) to a BRep shape."""
    trsf = gp_Trsf()
    origin = gp_Pnt(0, 0, 0)
    if rx != 0.0:
        t = gp_Trsf(); t.SetRotation(gp_Ax1(origin, gp_Dir(1, 0, 0)), math.radians(rx))
        trsf.Multiply(t)
    if ry != 0.0:
        t = gp_Trsf(); t.SetRotation(gp_Ax1(origin, gp_Dir(0, 1, 0)), math.radians(ry))
        trsf.Multiply(t)
    if rz != 0.0:
        t = gp_Trsf(); t.SetRotation(gp_Ax1(origin, gp_Dir(0, 0, 1)), math.radians(rz))
        trsf.Multiply(t)
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def import_step(path: str):
    """Load a STEP file and return its root shape."""
    logger.info(f"Import STEP: {path}")
    reader = STEPControl_Reader()
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        raise ValueError(f"Errore lettura STEP: {path}")
    reader.TransferRoots()
    return reader.OneShape()


def import_stl(path: str, auto_fix=True):
    """Load an STL file as a trimesh object, optionally applying mesh repairs."""
    logger.info(f"Import STL: {path}")
    mesh = trimesh.load_mesh(path)
    if auto_fix:
        mesh = fix_stl(mesh)
    return mesh


def fix_stl(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Apply basic mesh cleanup (normals, winding, holes, orphan vertices)."""
    logger.info("Fix STL running...")
    trimesh.repair.fix_normals(mesh)
    trimesh.repair.fill_holes(mesh)
    trimesh.repair.fix_winding(mesh)
    mesh.remove_unreferenced_vertices()
    return mesh




def _merge_planar_faces(mesh, angle_tol=1e-2):
    """Build one OCC face per triangle (safe fallback, no planar aggregation)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
    polygons = []
    for face in mesh.faces:
        v0, v1, v2 = [mesh.vertices[vi] for vi in face]
        poly = BRepBuilderAPI_MakePolygon(gp_Pnt(*v0), gp_Pnt(*v1), gp_Pnt(*v2), True)
        f = BRepBuilderAPI_MakeFace(poly.Wire(), True)
        if f.IsDone():
            polygons.append(f.Face())
    return polygons

def mesh_to_brep(mesh: trimesh.Trimesh):
    """Convert a triangular mesh into a sewn BRep shell.

    This path remains conservative: triangle-wise face creation, sewing, and an
    optional domain unification attempt.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    logger.info(f"mesh_to_brep: {len(mesh.faces)} triangoli")
    # 1) Build candidate faces from mesh triangles.
    all_faces = _merge_planar_faces(mesh)
    if not all_faces:
        logger.warning("Merging planare fallito o non applicabile, uso triangoli originali")
        # Fallback: rebuild using direct triangle conversion.
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
        for face in mesh.faces:
            try:
                v0, v1, v2 = [mesh.vertices[vi] for vi in face]
                poly = BRepBuilderAPI_MakePolygon(gp_Pnt(*v0), gp_Pnt(*v1), gp_Pnt(*v2), True)
                f = BRepBuilderAPI_MakeFace(poly.Wire(), True)
                if f.IsDone():
                    all_faces.append(f.Face())
            except: pass
    # 2) Sew faces into a single shell-like shape.
    sewing = BRepBuilderAPI_Sewing(1e-2)
    for f in all_faces:
        sewing.Add(f)
    sewing.Perform()
    sewn = sewing.SewedShape()
    # 3) Try same-domain unification to simplify contiguous regions.
    logger.info("mesh_to_brep: unificazione facce (UnifySameDomain)...")
    try:
        unify = ShapeUpgrade_UnifySameDomain(sewn, True, True, True)
        unify.Build()
        result = unify.Shape()
        BRepLib.BuildCurves3d_s(result)
    except Exception as e:
        logger.warning(f"Semplificazione fallita: {e}")
        result = sewn

    return result

class Model:
    """Container for imported geometry (BRep and/or mesh)."""
    def __init__(self, brep=None, mesh=None):
        self.brep = brep; self.mesh = mesh

    @classmethod
    def from_step(cls, path, rx=0.0, ry=0.0, rz=0.0):
        """Create a model from STEP, applying optional rotations."""
        brep = import_step(path)
        if any([rx, ry, rz]): brep = apply_rotation(brep, rx, ry, rz)
        return cls(brep=brep)

    @classmethod
    def from_stl(cls, path, convert_to_brep=True):
        """Create a model from STL in mesh-first mode.

        The ``convert_to_brep`` argument is currently retained for API
        compatibility. STL import returns mesh-only to keep the workflow robust.
        """
        mesh = import_stl(path)
        # BRep conversion intentionally disabled in the default STL path.
        return cls(mesh=mesh)

def validate_stl(mesh):
    """Return a compact mesh summary used by diagnostics/tests."""
    return {"vertices": len(mesh.vertices), "faces": len(mesh.faces)}

def is_multibody(mesh):
    """Detect whether a mesh contains multiple disconnected components."""
    return len(mesh.split(only_watertight=False)) > 1
