from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone, IFSelect_ItemsByEntity
from OCP.BRep import BRep_Builder
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.gp import gp_Trsf, gp_Ax1, gp_Pnt, gp_Dir, gp_EulerSequence
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
import math
import trimesh
import trimesh.repair
from antcam.logger import setup_logger
logger = setup_logger()


def apply_rotation(shape, rx: float = 0.0, ry: float = 0.0, rz: float = 0.0):
    """Applica una rotazione al BRep (angoli in gradi, ordine X→Y→Z)."""
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
        logger.error(f"Errore lettura STEP: {path}")
        raise ValueError(f"Errore lettura STEP: {path}")
    reader.TransferRoots()
    shape = reader.OneShape()
    return shape


def import_stl(path: str, auto_fix=True):
    logger.info(f"Import STL: {path}")
    mesh = trimesh.load_mesh(path)

    # Validazione pre-fix
    report_before = validate_stl(mesh)
    logger.info("Validazione STL (prima del fix)")
    for k, v in report_before.items():
        logger.info(f"  {k}: {v}")

    if auto_fix:
        mesh = fix_stl(mesh)
        # Validazione post-fix
        report_after = validate_stl(mesh)
        logger.info("Validazione STL (dopo il fix)")
        for k, v in report_after.items():
            logger.info(f"  {k}: {v}")

    return mesh


def fix_stl(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    logger.info("Fix STL running...")
    flag_multibody = is_multibody(mesh)
    trimesh.repair.fix_normals(mesh, multibody=flag_multibody)
    trimesh.repair.fill_holes(mesh)
    trimesh.repair.fix_winding(mesh)
    trimesh.repair.fix_inversion(mesh, multibody=flag_multibody)
    mesh.remove_unreferenced_vertices()
    mesh.remove_infinite_values()

    try:
        mesh = mesh.union(mesh, engine="manifold", check_volume=False)
        logger.info("Union manifold completed (check_volume deactivated)")
    except Exception as e:
        logger.error(f"Union manifold fallita: {e}")

    if not mesh.is_volume:
        logger.warning("Mesh non manifold/chiusa anche dopo il fix")

    logger.info("Fix STL completato")
    return mesh


def is_multibody(mesh: trimesh.Trimesh) -> bool:
    """
    Verifica se una mesh STL contiene più corpi separati.
    """
    # split in connected components
    components = mesh.split(only_watertight=False)
    return len(components) > 1


def count_bodies(mesh: trimesh.Trimesh) -> int:
    """
    Conta quanti corpi separati ci sono nella mesh STL.
    """
    components = mesh.split(only_watertight=False)
    return len(components)


def validate_stl(mesh: trimesh.Trimesh) -> dict:
    return {
        "vertices": len(mesh.vertices),
        "faces": len(mesh.faces),
        "is_watertight": mesh.is_watertight,
        "is_volume": mesh.is_volume,
        "num_bodies": len(mesh.split(only_watertight=False))
    }


class Model:
    def __init__(self, brep=None, mesh=None):
        self.brep = brep
        self.mesh = mesh

    @classmethod
    def from_step(cls, path, rx: float = 0.0, ry: float = 0.0, rz: float = 0.0):
        brep = import_step(path)
        if rx != 0.0 or ry != 0.0 or rz != 0.0:
            brep = apply_rotation(brep, rx, ry, rz)
        return cls(brep=brep)

    @classmethod
    def from_stl(cls, path):
        return cls(mesh=import_stl(path))

