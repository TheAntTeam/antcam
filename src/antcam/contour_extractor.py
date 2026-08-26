import logging
from typing import List, Tuple, Optional

"""2.5D contour extraction utilities from top-accessible BRep faces.

The extractor projects accessible faces onto a plane aligned with the tool axis,
fuses projected regions, and optionally subtracts through-hole footprints.
"""

import numpy as np

from OCP.TopExp import TopExp_Explorer, TopExp
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_WIRE
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
from OCP.BRepTools import BRepTools
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.gp import gp_Pnt, gp_Dir, gp_Lin, gp_Pln, gp_Ax3, gp_Ax2, gp_Circ
from OCP.TopoDS import TopoDS, TopoDS_Shape, TopoDS_Compound
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.GeomLProp import GeomLProp_SLProps
from OCP.BRepProj import BRepProj_Projection

logger = logging.getLogger("antcam")


class ContourExtractor:
    """Build projected shadow contours from the current model and feature set."""
    def __init__(self, model, working_plane_normal: Tuple[float, float, float] = (0.0, 0.0, 1.0), features=None):
        self.model = model
        self.working_plane_normal = np.array(working_plane_normal, dtype=float)
        self.working_plane_normal /= np.linalg.norm(self.working_plane_normal)
        self._min_proj_z = 0.0
        self._features = features or []

    def extract(self) -> Optional[TopoDS_Shape]:
        """Return the projected 2D shadow shape on the minimum tool-axis plane."""
        brep = self.model.brep
        if brep is None:
            logger.warning("Nessun BRep disponibile: salto estrazione contour (probabile STL)")
            return None
        self._compute_bbox(brep)

        accessible_faces = self._collect_accessible_faces(brep)
        logger.info(f"ContourExtractor: {len(accessible_faces)} facce accessibili")
        if not accessible_faces:
            return None

        plane_face, plane = self._make_projection_plane()
        plane_faces = self._project_faces_to_plane(accessible_faces, plane_face, plane)
        logger.info(f"ContourExtractor: {len(plane_faces)} facce proiettate")
        if not plane_faces:
            return None

        fused = self._fuse_faces(plane_faces)

        # Sottrae i fori passanti dalla shape ombra
        fused = self._cut_through_holes(fused, plane)

        logger.info(f"ContourExtractor: shape ombra costruita")
        return fused

    def extract_perimeter(self, shadow: TopoDS_Shape) -> List[TopoDS_Shape]:
        """Extract free boundary wires from a shadow shape."""
        from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
        from OCP.TopAbs import TopAbs_WIRE
        try:
            sab = ShapeAnalysis_FreeBounds(shadow)
            wires = []
            for compound in (sab.GetClosedWires(), sab.GetOpenWires()):
                exp = TopExp_Explorer(compound, TopAbs_WIRE)
                while exp.More():
                    wires.append(exp.Current())
                    exp.Next()
            logger.info(f"ContourExtractor: {len(wires)} wire perimetro")
            return wires
        except Exception as e:
            logger.warning(f"extract_perimeter error: {e}")
            return []
    # ------------------------------------------------------------------

    def _collect_accessible_faces(self, brep) -> list:
        """Collect faces that are reachable along the tool axis direction."""
        accessible = []
        exp = TopExp_Explorer(brep, TopAbs_FACE)
        while exp.More():
            face = exp.Current()
            if self._face_is_accessible(face):
                accessible.append(face)
            exp.Next()
        return accessible

    def _face_is_accessible(self, face) -> bool:
        """Heuristic top-accessibility check based on normal and ray obstruction."""
        try:
            face_s = TopoDS.Face_s(face)
            surf = BRepAdaptor_Surface(face_s, True)
            u = (surf.FirstUParameter() + surf.LastUParameter()) / 2
            v = (surf.FirstVParameter() + surf.LastVParameter()) / 2
            pnt = surf.Value(u, v)

            geom_surf = BRep_Tool.Surface_s(face_s)
            props = GeomLProp_SLProps(geom_surf, u, v, 1, 1e-6)
            if not props.IsNormalDefined():
                return False
            occ_n = props.Normal()
            normal = np.array([occ_n.X(), occ_n.Y(), occ_n.Z()])
            if face_s.Orientation() == TopAbs_REVERSED:
                normal = -normal

            n = self.working_plane_normal
            dot = np.dot(normal, n)
            if dot < 0.05:
                return False

            # Ray cast starts slightly above the face along the tool axis.
            n_arr = n
            proj = pnt.X()*n_arr[0] + pnt.Y()*n_arr[1] + pnt.Z()*n_arr[2]
            # Small offset avoids immediately re-hitting the same face.
            eps = 0.05
            start = gp_Pnt(
                pnt.X() + n_arr[0]*eps,
                pnt.Y() + n_arr[1]*eps,
                pnt.Z() + n_arr[2]*eps
            )
            line = gp_Lin(start, gp_Dir(n_arr[0], n_arr[1], n_arr[2]))
            inter = BRepIntCurveSurface_Inter()
            inter.Init(self.model.brep, line, 1e-4)
            while inter.More():
                w = inter.W()
                if w > eps * 0.5:  # Ignore near-self intersections.
                    return False
                inter.Next()
            return True
        except Exception:
            return False

    def _make_projection_plane(self):
        """Create a stable projection plane anchored at minimum tool-axis depth."""
        n = self.working_plane_normal
        z_min = self._min_proj_z
        ref = np.array([1.0, 0.0, 0.0])
        if abs(np.dot(ref, n)) > 0.9:
            ref = np.array([0.0, 1.0, 0.0])
        x_dir = ref - np.dot(ref, n) * n
        x_dir /= np.linalg.norm(x_dir)
        ax3 = gp_Ax3(
            gp_Pnt(n[0]*z_min, n[1]*z_min, n[2]*z_min),
            gp_Dir(n[0], n[1], n[2]),
            gp_Dir(x_dir[0], x_dir[1], x_dir[2])
        )
        plane = gp_Pln(ax3)
        plane_face = BRepBuilderAPI_MakeFace(plane, -1e4, 1e4, -1e4, 1e4).Face()
        return plane_face, plane

    def _project_faces_to_plane(self, faces: list, plane_face, plane) -> list:
        """Project face wires to the target plane and rebuild planar faces."""
        n = self.working_plane_normal
        plane_faces = []
        for face in faces:
            projected_outer = None
            projected_inner_wires = []
            outer_wire = None
            try:
                outer_wire = BRepTools.OuterWire_s(TopoDS.Face_s(face))
            except Exception:
                outer_wire = None

            exp_w = TopExp_Explorer(face, TopAbs_WIRE)
            while exp_w.More():
                wire = TopoDS.Wire_s(exp_w.Current())
                try:
                    proj = BRepProj_Projection(
                        wire,
                        plane_face,
                        gp_Dir(n[0], n[1], n[2])
                    )
                    if proj.More():
                        proj_wire = TopoDS.Wire_s(proj.Current())
                        if outer_wire is not None and outer_wire.IsSame(wire):
                            projected_outer = proj_wire
                        else:
                            projected_inner_wires.append(proj_wire)
                except Exception:
                    pass
                exp_w.Next()

            if projected_outer is None:
                if not projected_inner_wires:
                    continue
                projected_outer = projected_inner_wires[0]
                projected_inner_wires = projected_inner_wires[1:]

            try:
                face_2d = BRepBuilderAPI_MakeFace(plane, projected_outer)
                if not face_2d.IsDone():
                    continue
                for inner_wire in projected_inner_wires:
                    face_2d.Add(inner_wire)
                plane_faces.append(face_2d.Face())
            except Exception:
                pass
        return plane_faces

    def _fuse_faces(self, plane_faces: list) -> TopoDS_Shape:
        """Fuse projected faces; fallback to a compound when fusion is unstable."""
        if len(plane_faces) == 1:
            return plane_faces[0]
        # Keep a compound fallback to guarantee a drawable result.
        builder = BRep_Builder()
        compound = TopoDS_Compound()
        builder.MakeCompound(compound)
        for f in plane_faces:
            builder.Add(compound, f)
        try:
            result = plane_faces[0]
            for face in plane_faces[1:]:
                fuse = BRepAlgoAPI_Fuse(result, face)
                fuse.Build()
                if fuse.IsDone() and not fuse.Shape().IsNull():
                    result = fuse.Shape()
                else:
                    return compound
            return result
        except Exception:
            return compound

    def _cut_through_holes(self, shadow: TopoDS_Shape, plane) -> TopoDS_Shape:
        """Subtract through-hole disks from the projected shadow shape."""
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.GeomAbs import GeomAbs_Cylinder
        n = self.working_plane_normal
        z_min = self._min_proj_z
        result = shadow

        n_through = sum(1 for f in self._features if f.type == "hole_group" and f.props.get("through"))
        n_total_groups = sum(1 for f in self._features if f.type == "hole_group")
        logger.info(f"ContourExtractor: {n_through}/{n_total_groups} hole_group passanti")

        for feat in self._features:
            if feat.type != "hole_group" or not feat.props.get("through"):
                continue
            for hole in feat.holes:
                if not hole.geometry:
                    continue
                try:
                    surf = BRepAdaptor_Surface(TopoDS.Face_s(hole.geometry), True)
                    if surf.GetType() != GeomAbs_Cylinder:
                        continue
                    cyl = surf.Cylinder()
                    loc = cyl.Axis().Location()
                    radius = cyl.Radius()

                    proj = loc.X()*n[0] + loc.Y()*n[1] + loc.Z()*n[2]
                    delta = z_min - proj
                    center = gp_Pnt(
                        loc.X() + n[0]*delta,
                        loc.Y() + n[1]*delta,
                        loc.Z() + n[2]*delta,
                    )
                    ax2 = gp_Ax2(center, gp_Dir(n[0], n[1], n[2]))
                    circle = gp_Circ(ax2, radius)
                    edge = BRepBuilderAPI_MakeEdge(circle).Edge()
                    wire = BRepBuilderAPI_MakeWire(edge).Wire()
                    disc = BRepBuilderAPI_MakeFace(plane, wire)
                    if not disc.IsDone():
                        continue
                    cut = BRepAlgoAPI_Cut(result, disc.Face())
                    cut.Build()
                    if cut.IsDone() and not cut.Shape().IsNull():
                        result = cut.Shape()
                except Exception:
                    pass
        return result

    def _compute_bbox(self, brep):
        bbox = Bnd_Box()
        BRepBndLib.Add_s(brep, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        corners = [
            np.array([xmin, ymin, zmin]), np.array([xmax, ymin, zmin]),
            np.array([xmin, ymax, zmin]), np.array([xmax, ymax, zmin]),
            np.array([xmin, ymin, zmax]), np.array([xmax, ymin, zmax]),
            np.array([xmin, ymax, zmax]), np.array([xmax, ymax, zmax]),
        ]
        self._min_proj_z = min(np.dot(c, self.working_plane_normal) for c in corners)
