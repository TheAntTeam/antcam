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

logger = setup_logger()

def apply_rotation(shape, rx: float = 0.0, ry: float = 0.0, rz: float = 0.0):
    """Applica una rotazione al BRep."""
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
    logger.info(f"Import STEP: {path}")
    reader = STEPControl_Reader()
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        raise ValueError(f"Errore lettura STEP: {path}")
    reader.TransferRoots()
    return reader.OneShape()


def import_stl(path: str, auto_fix=True):
    logger.info(f"Import STL: {path}")
    mesh = trimesh.load_mesh(path)
    if auto_fix:
        mesh = fix_stl(mesh)
    return mesh


def fix_stl(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    logger.info("Fix STL running...")
    trimesh.repair.fix_normals(mesh)
    trimesh.repair.fill_holes(mesh)
    trimesh.repair.fix_winding(mesh)
    mesh.remove_unreferenced_vertices()
    return mesh




def _merge_planar_faces(mesh, angle_tol=1e-2):
    """Crea una faccia OCC per ogni triangolo della mesh (nessun merging)."""
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
    """Converte mesh in BRep con merging planare avanzato."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    logger.info(f"mesh_to_brep: {len(mesh.faces)} triangoli")
    # 1. Prova merging planare avanzato
    all_faces = _merge_planar_faces(mesh)
    if not all_faces:
        logger.warning("Merging planare fallito o non applicabile, uso triangoli originali")
        # Fallback: triangoli
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
        for face in mesh.faces:
            try:
                v0, v1, v2 = [mesh.vertices[vi] for vi in face]
                poly = BRepBuilderAPI_MakePolygon(gp_Pnt(*v0), gp_Pnt(*v1), gp_Pnt(*v2), True)
                f = BRepBuilderAPI_MakeFace(poly.Wire(), True)
                if f.IsDone():
                    all_faces.append(f.Face())
            except: pass
    # 2. Cucitura
    sewing = BRepBuilderAPI_Sewing(1e-2)
    for f in all_faces:
        sewing.Add(f)
    sewing.Perform()
    sewn = sewing.SewedShape()
    # 3. Unificazione avanzata
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
    def __init__(self, brep=None, mesh=None):
        self.brep = brep; self.mesh = mesh

    @classmethod
    def from_step(cls, path, rx=0.0, ry=0.0, rz=0.0):
        brep = import_step(path)
        if any([rx, ry, rz]): brep = apply_rotation(brep, rx, ry, rz)
        return cls(brep=brep)

    @classmethod
    def from_stl(cls, path, convert_to_brep=True):
        mesh = import_stl(path)
        # Conversione a BRep disattivata: restituisce solo la mesh
        return cls(mesh=mesh)

def validate_stl(mesh):
    return {"vertices": len(mesh.vertices), "faces": len(mesh.faces)}

def is_multibody(mesh):
    return len(mesh.split(only_watertight=False)) > 1
