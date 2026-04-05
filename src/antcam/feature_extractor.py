import logging
import math
from dataclasses import dataclass, field
from typing import List, Any, Optional, Tuple, Dict, Union

import trimesh
import numpy as np

from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_REVERSED, TopAbs_SHAPE
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import (
    GeomAbs_Plane,
    GeomAbs_Cylinder,
    GeomAbs_Cone,
    GeomAbs_Sphere,
    GeomAbs_Torus
)
from OCP.BRep import BRep_Tool
from OCP.TopoDS import TopoDS, TopoDS_Face
from OCP.gp import gp_Pnt, gp_Dir, gp_Vec, gp_Lin
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter

logger = logging.getLogger("antcam")


# ============================================================
# FEATURE DATA STRUCTURES
# ============================================================

@dataclass
class Feature:
    type: str
    geometry: Any = None
    props: dict = field(default_factory=dict)

    def __repr__(self):
        return f"<Feature type={self.type} props={self.props}>"


@dataclass
class HoleFeature(Feature):
    def __init__(self, diameter: float, axis: Tuple[float, float, float],
                 center: Tuple[float, float, float], depth: float, through: bool = False, face: Any = None):
        super().__init__("hole", geometry=face, props={
            "diameter": diameter, "axis": axis, "center": center, "depth": depth, "through": through,
        })

@dataclass
class HoleGroup(Feature):
    def __init__(self, diameter: float, depth: float, through: bool, holes: List):
        super().__init__("hole_group", geometry=None, props={
            "diameter": diameter, "depth": depth, "through": through,
            "count": len(holes), "tap_candidate": True,
        })
        self.holes = holes

@dataclass
class CountersunkHoleFeature(Feature):
    def __init__(self, diameter: float, axis: Tuple[float, float, float], 
                 center: Tuple[float, float, float], depth: float,
                 cs_diameter: float, cs_angle: float, face: Any = None):
        super().__init__("countersunk_hole", geometry=face, props={
            "diameter": diameter, "axis": axis, "center": center, "depth": depth, "cs_diameter": cs_diameter, "cs_angle": cs_angle,
        })

@dataclass
class PocketFeature(Feature):
    def __init__(self, depth: float, bottom_plane: Any, normal: Tuple[float, float, float]):
        super().__init__("pocket", geometry=bottom_plane, props={"depth": depth, "bottom_normal": normal})

@dataclass
class StepFeature(Feature):
    def __init__(self, depth: float, bottom_plane: Any, open_edges_count: int):
        super().__init__("step", geometry=bottom_plane, props={"depth": depth, "open_edges": open_edges_count})

@dataclass
class SlotFeature(Feature):
    def __init__(self, width: float, length: float, axis: Tuple[float, float, float],
                 center: Tuple[float, float, float], depth: float, through: bool = False, face: Any = None):
        super().__init__("slot", geometry=face, props={
            "width": width, "length": length, "axis": axis, "center": center, "depth": depth, "through": through,
        })

@dataclass
class OpeningFeature(Feature):
    def __init__(self, depth: float, through: bool, bottom_plane: Any):
        super().__init__("opening", geometry=bottom_plane, props={"depth": depth, "through": through})

@dataclass
class FilletFeature(Feature):
    def __init__(self, radius: float, is_concave: bool, face: Any = None):
        super().__init__("fillet", geometry=face, props={"radius": radius, "concave": is_concave})

@dataclass
class ChamferFeature(Feature):
    def __init__(self, angle: float, width: float, face: Any = None):
        super().__init__("chamfer", geometry=face, props={"angle": angle, "width": width})


# ============================================================
# FEATURE EXTRACTOR
# ============================================================

class FeatureExtractor:
    def __init__(self, model, working_plane_normal: Tuple[float, float, float] = (0.0, 0.0, 1.0)):
        self.model = model
        self.features: List[Feature] = []
        self._bbox = None
        self._edge_to_faces = None
        self.working_plane_normal = np.array(working_plane_normal)
        self.working_plane_normal /= np.linalg.norm(self.working_plane_normal)
        self._max_proj_z = 0.0
        self._min_proj_z = 0.0

    def extract(self) -> List[Feature]:
        logger.info(f"Extracting features (Tool Axis: {self.working_plane_normal})")
        self.features = []
        if not self.model or not hasattr(self.model, 'brep'): raise ValueError("Model BRep not available")
        
        self._calculate_brep_bbox(self.model.brep)
        self._edge_to_faces = self._map_edge_to_faces(self.model.brep)

        # Mappa stabile edge->facce per _cylinder_has_real_bottom
        from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
        self._edge_face_map = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(self.model.brep, TopAbs_EDGE, TopAbs_FACE, self._edge_face_map)
        
        faces = self._get_faces(self.model.brep)
        cylinders, cones = [], []

        for face in faces:
            topo_face = TopoDS.Face_s(face)
            surf = BRepAdaptor_Surface(topo_face, True)
            stype = surf.GetType()

            if stype == GeomAbs_Cylinder:
                data = self._analyze_cylindrical_face(topo_face, surf)
                if data:
                    if data.get("is_fillet") and self._is_cylinder_obstructed(topo_face, surf):
                        pass
                    else:
                        cylinders.append(data)
            elif stype == GeomAbs_Cone:
                data = self._analyze_conical_face(topo_face, surf)
                if data: cones.append(data)
            elif stype == GeomAbs_Plane:
                if self._is_face_obstructed(topo_face, self.model.brep):
                    continue
                plane_feature = self._analyze_planar_face_topologicamente(topo_face, surf)
                if plane_feature is None:
                    plane_feature = self._analyze_chamfer_face(topo_face, surf)
                if plane_feature: self.features.append(plane_feature)

        self._group_composite_features(cylinders, cones)
        self._arc_groups = self.find_vertical_faces_with_xy_arcs()
        self._find_holes_from_arc_groups(self._arc_groups)
        self._group_holes()
        return self.features

    def _is_face_obstructed(self, face, brep) -> bool:
        """Per CNC 3 assi (2.5D): verifica se c'è materiale sopra la faccia lungo l'asse utensile."""
        surf = BRepAdaptor_Surface(face, True)
        u = (surf.FirstUParameter() + surf.LastUParameter()) / 2
        v = (surf.FirstVParameter() + surf.LastVParameter()) / 2
        pnt = surf.Value(u, v)

        # eps abbastanza grande da uscire dalla tolleranza geometrica del BRep
        # ma non così grande da saltare feature vicine
        eps = 0.01
        start_pnt = gp_Pnt(pnt.X() + self.working_plane_normal[0]*eps,
                           pnt.Y() + self.working_plane_normal[1]*eps,
                           pnt.Z() + self.working_plane_normal[2]*eps)
        direction = gp_Dir(self.working_plane_normal[0], self.working_plane_normal[1], self.working_plane_normal[2])
        line = gp_Lin(start_pnt, direction)

        inter = BRepIntCurveSurface_Inter()
        inter.Init(brep, line, 1e-4)
        # Scarta intersezioni troppo vicine al punto di partenza (stessa faccia)
        while inter.More():
            if inter.W() > eps * 10:
                return True
            inter.Next()
        return False

    def _is_cylinder_obstructed(self, face, surf) -> bool:
        """Per i fillet cilindrici: parte dal punto piu' alto del cilindro
        (quota massima lungo l'asse utensile) e verifica se c'e' materiale sopra."""
        # Campiona il bordo superiore del cilindro (massima proiezione sull'asse utensile)
        u1, u2 = surf.FirstUParameter(), surf.LastUParameter()
        v1, v2 = surf.FirstVParameter(), surf.LastVParameter()
        best_pnt = None
        best_proj = -1e9
        for ui in [u1, (u1+u2)/2, u2]:
            for vi in [v1, v2]:
                p = surf.Value(ui, vi)
                proj = (p.X()*self.working_plane_normal[0] +
                        p.Y()*self.working_plane_normal[1] +
                        p.Z()*self.working_plane_normal[2])
                if proj > best_proj:
                    best_proj = proj
                    best_pnt = p
        if best_pnt is None:
            return True
        eps = 0.01
        n = self.working_plane_normal
        start = gp_Pnt(best_pnt.X() + n[0]*eps,
                       best_pnt.Y() + n[1]*eps,
                       best_pnt.Z() + n[2]*eps)
        line = gp_Lin(start, gp_Dir(n[0], n[1], n[2]))
        inter = BRepIntCurveSurface_Inter()
        inter.Init(self.model.brep, line, 1e-4)
        while inter.More():
            if inter.W() > eps * 10:
                return True
            inter.Next()
        return False

    def _calculate_brep_bbox(self, shape):
        bbox = Bnd_Box()
        BRepBndLib.Add_s(shape, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        corners = [np.array([xmin, ymin, zmin]), np.array([xmax, ymin, zmin]), np.array([xmin, ymax, zmin]), np.array([xmax, ymax, zmin]),
                   np.array([xmin, ymin, zmax]), np.array([xmax, ymin, zmax]), np.array([xmin, ymax, zmax]), np.array([xmax, ymax, zmax])]
        self._max_proj_z = max(np.dot(c, self.working_plane_normal) for c in corners)
        self._min_proj_z = min(np.dot(c, self.working_plane_normal) for c in corners)

    def _map_edge_to_faces(self, shape):
        mapping = {}
        exp_faces = TopExp_Explorer(shape, TopAbs_FACE)
        while exp_faces.More():
            face = exp_faces.Current()
            exp_edges = TopExp_Explorer(face, TopAbs_EDGE)
            while exp_edges.More():
                edge = exp_edges.Current()
                h = hash(edge) if hasattr(edge, "__hash__") else edge.this
                if h not in mapping: mapping[h] = []
                mapping[h].append(face)
                exp_edges.Next()
            exp_faces.Next()
        return mapping

    def _analyze_cylindrical_face(self, face, surf) -> Optional[dict]:
        radius = surf.Cylinder().Radius()
        axis_dir = np.array((surf.Cylinder().Axis().Direction().X(), surf.Cylinder().Axis().Direction().Y(), surf.Cylinder().Axis().Direction().Z()))
        dot_axis = abs(np.dot(axis_dir, self.working_plane_normal))
        u1, u2 = surf.FirstUParameter(), surf.LastUParameter()
        u_range = abs(u2 - u1)
        depth = abs(surf.LastVParameter() - surf.FirstVParameter())
        center = (surf.Cylinder().Axis().Location().X(), surf.Cylinder().Axis().Location().Y(), surf.Cylinder().Axis().Location().Z())

        # Cilindro con asse parallelo all'utensile
        if dot_axis >= 0.99:
            is_full = u_range >= (2 * math.pi - 0.2)  # 360° = foro
            is_half = math.pi - 0.4 <= u_range <= math.pi + 0.4  # ~180° = semicilindro asola

            if is_full:
                # Foro: accessibile se REVERSED (cavita') o se e' un foro passante in un compound (FORWARD)
                # Distinguiamo cieco/passante: cieco se ha una faccia piana orizzontale adiacente
                # che e' a quota INFERIORE al bordo superiore del cilindro
                through = not self._cylinder_has_real_bottom(face, surf)
                return {"type": "cylinder", "radius": radius, "is_fillet": False,
                        "axis": tuple(axis_dir), "center": center, "depth": depth, "through": through, "face": face}

            if is_half:
                # Semicilindro con asse parallelo: potrebbe essere meta' di un foro passante
                # Lo trattiamo come arc_forward per il raggruppamento
                return {"type": "arc_forward", "radius": radius,
                        "axis": tuple(axis_dir), "center": center, "depth": depth,
                        "u_range": u_range, "face": face}

            # Arco parziale con asse parallelo = fillet angolare (es. raccordo angolo tasca)
            if face.Orientation() == TopAbs_REVERSED and radius < 15.0:
                return {"type": "cylinder", "radius": radius, "is_fillet": True,
                        "axis": tuple(axis_dir), "center": center, "depth": depth, "through": False, "face": face}
            return None

        # Semicilindro con asse nel piano di lavoro: possibile asola
        if dot_axis <= 0.1 and math.pi - 0.4 <= u_range <= math.pi + 0.4:
            if face.Orientation() == TopAbs_REVERSED:
                return {"type": "slot_half", "radius": radius,
                        "axis": tuple(axis_dir), "center": center, "depth": depth, "face": face}

        # Arco cilindrico FORWARD con asse parallelo: parte di asola o apertura passante
        if dot_axis >= 0.99 and face.Orientation().name == "TopAbs_FORWARD":
            return {"type": "arc_forward", "radius": radius,
                    "axis": tuple(axis_dir), "center": center, "depth": depth,
                    "u_range": u_range, "face": face}

        return None

    def _cylinder_has_real_bottom(self, face, surf) -> bool:
        """Un foro e' cieco se ha esattamente UNA faccia piana orizzontale adiacente
        che non e' ne' la faccia superiore ne' quella inferiore del pezzo.
        Se ha zero o due facce piane orizzontali adiacenti, e' passante."""
        v1, v2 = surf.FirstVParameter(), surf.LastVParameter()
        u_mid = (surf.FirstUParameter() + surf.LastUParameter()) / 2
        p_v1 = surf.Value(u_mid, v1)
        p_v2 = surf.Value(u_mid, v2)
        proj_v1 = np.dot([p_v1.X(), p_v1.Y(), p_v1.Z()], self.working_plane_normal)
        proj_v2 = np.dot([p_v2.X(), p_v2.Y(), p_v2.Z()], self.working_plane_normal)
        proj_bot = min(proj_v1, proj_v2)
        proj_top = max(proj_v1, proj_v2)

        # Raccoglie indici edge del cilindro
        cyl_edge_indices = set()
        exp = TopExp_Explorer(face, TopAbs_EDGE)
        while exp.More():
            idx = self._edge_face_map.FindIndex(exp.Current())
            if idx > 0:
                cyl_edge_indices.add(idx)
            exp.Next()

        # Conta le facce piane orizzontali adiacenti al cilindro
        horiz_faces_at_bot = 0
        horiz_faces_at_top = 0
        exp_f = TopExp_Explorer(self.model.brep, TopAbs_FACE)
        while exp_f.More():
            nf = exp_f.Current()
            nf_s = BRepAdaptor_Surface(TopoDS.Face_s(nf), True)
            if nf_s.GetType() == GeomAbs_Plane:
                nf_n = np.array([nf_s.Plane().Axis().Direction().X(),
                                 nf_s.Plane().Axis().Direction().Y(),
                                 nf_s.Plane().Axis().Direction().Z()])
                if abs(np.dot(nf_n, self.working_plane_normal)) >= 0.99:
                    # e' una faccia piana
                    nf_loc = nf_s.Plane().Axis().Location()
                    nf_proj = np.dot([nf_loc.X(), nf_loc.Y(), nf_loc.Z()],
                                     self.working_plane_normal)
                    # shares_edge = False
                    # exp_e = TopExp_Explorer(nf, TopAbs_EDGE)
                    shares_edge = True
                    # while exp_e.More():
                    #     if self._edge_face_map.FindIndex(exp_e.Current()) in cyl_edge_indices:
                    #         shares_edge = True
                    #         break
                    #     exp_e.Next()
                    if shares_edge:
                        is_at_bot = abs(nf_proj - proj_bot) < 0.1
                        is_at_top = abs(nf_proj - proj_top) < 0.1
                        is_piece_top = abs(nf_proj - self._max_proj_z) < 0.1
                        is_piece_bot = abs(nf_proj - self._min_proj_z) < 0.1
                        if is_at_bot and not is_piece_top:
                            horiz_faces_at_bot += 1
                        elif is_at_top and not is_piece_top:
                            horiz_faces_at_top += 1
            exp_f.Next()

        # Cieco: ha una faccia piana al fondo (proj_bot) ma non all'ingresso (proj_top)
        if horiz_faces_at_bot > 0 and horiz_faces_at_top == 0:
            if abs(proj_bot - self._min_proj_z) > 0.1:
                return True
        return False

    def _stable_neighbors(self, edge_idx: int):
        """Non usato - mantenuto per compatibilità."""
        return []

    def _cylinder_has_bottom(self, face) -> bool:
        """Verifica se il cilindro ha una faccia piana di fondo (usata per slot)."""
        exp = TopExp_Explorer(face, TopAbs_EDGE)
        while exp.More():
            h = hash(exp.Current()) if hasattr(exp.Current(), "__hash__") else exp.Current().this
            for nf in self._edge_to_faces.get(h, []):
                if nf == face: continue
                nf_s = BRepAdaptor_Surface(TopoDS.Face_s(nf), True)
                if nf_s.GetType() == GeomAbs_Plane:
                    nf_n = np.array((nf_s.Plane().Axis().Direction().X(), nf_s.Plane().Axis().Direction().Y(), nf_s.Plane().Axis().Direction().Z()))
                    if abs(np.dot(nf_n, self.working_plane_normal)) >= 0.99:
                        return True
            exp.Next()
        return False

    def _plane_has_floor(self, face, proj_z: float) -> bool:
        """True se la cavità è cieca (la faccia è il fondo reale).
        Controlla se le pareti verticali adiacenti hanno tutti gli edge condivisi
        (nessun bordo libero in basso = cavità chiusa = cieca).
        """
        exp = TopExp_Explorer(face, TopAbs_EDGE)
        while exp.More():
            h = hash(exp.Current()) if hasattr(exp.Current(), "__hash__") else exp.Current().this
            for nf in self._edge_to_faces.get(h, []):
                if nf == face: continue
                nf_s = BRepAdaptor_Surface(TopoDS.Face_s(nf), True)
                if nf_s.GetType() != GeomAbs_Plane: continue
                nf_n = np.array((nf_s.Plane().Axis().Direction().X(), nf_s.Plane().Axis().Direction().Y(), nf_s.Plane().Axis().Direction().Z()))
                if abs(np.dot(nf_n, self.working_plane_normal)) > 0.05: continue
                # Parete verticale adiacente: ha edge liberi?
                exp2 = TopExp_Explorer(nf, TopAbs_EDGE)
                while exp2.More():
                    eh = hash(exp2.Current()) if hasattr(exp2.Current(), "__hash__") else exp2.Current().this
                    if len(self._edge_to_faces.get(eh, [])) < 2:
                        return False  # bordo libero -> passante
                    exp2.Next()
            exp.Next()
        return True  # tutte le pareti chiuse -> cieca


    def _analyze_conical_face(self, face, surf) -> Optional[dict]:
        # Rimosso il controllo iniziale face.Orientation() != TopAbs_REVERSED
        cone = surf.Cone()
        axis_dir = np.array((cone.Axis().Direction().X(), cone.Axis().Direction().Y(), cone.Axis().Direction().Z()))
        
        # Filtro Rigido: il cono deve aprirsi verso l'utensile
        # Calcoliamo i raggi alle estremità V
        v1, v2 = surf.FirstVParameter(), surf.LastVParameter()
        r1 = cone.RefRadius() + v1 * math.tan(cone.SemiAngle())
        r2 = cone.RefRadius() + v2 * math.tan(cone.SemiAngle())
        
        # Se r2 < r1, il cono si allarga verso +V. L'asse punta verso +V.
        # Invertiamo l'asse se il cono si restringe nella direzione dell'asse utensile
        if r2 < r1: 
            axis_dir = -axis_dir # Invertiamo per avere il vettore "apertura"
        
        # Il cono deve aprirsi nella direzione dell'utensile (o essere parallelo)
        if np.dot(axis_dir, self.working_plane_normal) < 0.99: return None

        return {"type": "cone", "angle": math.degrees(cone.SemiAngle()), "is_chamfer": abs(surf.LastUParameter() - surf.FirstUParameter()) < 6.0,
                "radii": (r1, r2), "axis": tuple(axis_dir), "center": (cone.Axis().Location().X(), cone.Axis().Location().Y(), cone.Axis().Location().Z()), "face": face}

    def _analyze_planar_face_topologicamente(self, face, surf) -> Optional[Feature]:
        axis = surf.Plane().Axis()
        normal = np.array((axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z()))
        dot = np.dot(normal, self.working_plane_normal)

        # Faccia orizzontale (fondo tasca / gradino / top)
        # abs(dot): la normale OCC può puntare verso il basso per facce superiori
        if abs(dot) >= 0.99:
            proj_z = np.dot(np.array((axis.Location().X(), axis.Location().Y(), axis.Location().Z())), self.working_plane_normal)
            open_edges = 0
            exp = TopExp_Explorer(face, TopAbs_EDGE)
            while exp.More():
                edge = exp.Current()
                h = hash(edge) if hasattr(edge, "__hash__") else edge.this
                neighbors = self._edge_to_faces.get(h, [])
                if len(neighbors) < 2:
                    open_edges += 1
                else:
                    is_wall = False
                    for nf in neighbors:
                        if nf == face: continue
                        nf_s = BRepAdaptor_Surface(TopoDS.Face_s(nf), True)
                        if nf_s.GetType() == GeomAbs_Plane:
                            nf_n = np.array((nf_s.Plane().Axis().Direction().X(), nf_s.Plane().Axis().Direction().Y(), nf_s.Plane().Axis().Direction().Z()))
                            if abs(np.dot(nf_n, self.working_plane_normal)) < 0.05: is_wall = True; break
                        elif nf_s.GetType() in [GeomAbs_Cylinder, GeomAbs_Cone]: is_wall = True; break
                    if not is_wall: open_edges += 1
                exp.Next()
            depth = self._max_proj_z - proj_z
            if open_edges > 0:
                return StepFeature(depth=depth, bottom_plane=face, open_edges_count=open_edges)
            # Pocket chiusa: verifica se è passante (opening) o cieca (pocket)
            # È passante se nessun vicino è una faccia orizzontale più in basso (nessun fondo reale)
            through = not self._plane_has_floor(face, proj_z)
            if through:
                return OpeningFeature(depth=depth, through=True, bottom_plane=face)
            return PocketFeature(depth=depth, bottom_plane=face, normal=tuple(normal))

        return None  # Pareti verticali non lavorabili in 2.5D

    def _analyze_chamfer_face(self, face, surf) -> Optional[Feature]:
        """Riconosce un chamfer piano: faccia inclinata confinante con una faccia orizzontale e una verticale."""
        axis = surf.Plane().Axis()
        normal = np.array((axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z()))
        dot = abs(np.dot(normal, self.working_plane_normal))
        # Chamfer tipico: angolo tra 10° e 80° rispetto all'asse utensile
        if dot < 0.17 or dot > 0.98:
            return None

        has_horizontal_neighbor = False
        has_vertical_neighbor = False
        exp = TopExp_Explorer(face, TopAbs_EDGE)
        while exp.More():
            edge = exp.Current()
            h = hash(edge) if hasattr(edge, "__hash__") else edge.this
            for nf in self._edge_to_faces.get(h, []):
                if nf == face: continue
                nf_s = BRepAdaptor_Surface(TopoDS.Face_s(nf), True)
                if nf_s.GetType() == GeomAbs_Plane:
                    nf_dot = abs(np.dot(
                        np.array((nf_s.Plane().Axis().Direction().X(), nf_s.Plane().Axis().Direction().Y(), nf_s.Plane().Axis().Direction().Z())),
                        self.working_plane_normal
                    ))
                    if nf_dot >= 0.99: has_horizontal_neighbor = True
                    elif nf_dot <= 0.05: has_vertical_neighbor = True
            exp.Next()

        if not (has_horizontal_neighbor and has_vertical_neighbor):
            return None

        angle_deg = math.degrees(math.acos(np.clip(dot, 0.0, 1.0)))
        # Larghezza: distanza tra i parametri V estremi (altezza della faccia inclinata)
        v_range = abs(surf.LastVParameter() - surf.FirstVParameter())
        u_range = abs(surf.LastUParameter() - surf.FirstUParameter())
        width = min(v_range, u_range)
        return ChamferFeature(angle=angle_deg, width=width, face=face)

    def _group_composite_features(self, cylinders, cones):
        used_cones, used_cyls = set(), set()

        # Separa cilindri normali da semicilindri di asola e archi forward
        slot_halves = [c for c in cylinders if c["type"] == "slot_half"]
        arc_forwards = [c for c in cylinders if c["type"] == "arc_forward"]
        normal_cyls = [(i, c) for i, c in enumerate(cylinders) if c["type"] == "cylinder"]

        # Raggruppa arc_forward coassiali con stesso raggio -> slot o opening
        used_arcs = set()
        arc_groups: Dict[tuple, List] = {}
        for i, arc in enumerate(arc_forwards):
            key = None
            for k in arc_groups:
                k_radius, k_axis, k_center = k
                if abs(arc["radius"] - k_radius) > 0.1: continue
                if abs(np.dot(arc["axis"], k_axis)) < 0.99: continue
                # Stesso asse e stesso raggio: stesso foro/asola
                arc_groups[k].append(arc)
                key = k
                break
            if key is None:
                key = (round(arc["radius"], 2), arc["axis"], arc["center"])
                arc_groups[key] = [arc]

        for key, arcs in arc_groups.items():
            total_u = sum(a["u_range"] for a in arcs)
            depth = arcs[0]["depth"]
            through = not self._cylinder_has_bottom(arcs[0]["face"])
            centers = [np.array(a["center"]) for a in arcs]
            # Fori (2pi) e semicilindri (pi): gestiti da _find_holes_from_arc_groups
            if abs(total_u - 2 * math.pi) < 0.5 or (len(arcs) == 1 and abs(total_u - math.pi) < 0.3):
                pass
            else:
                # Asola: due gruppi di archi a centri diversi
                if len(centers) >= 2:
                    c1, c2 = centers[0], centers[-1]
                    dist = np.linalg.norm(c1 - c2)
                    slot_center = tuple((c1 + c2) / 2)
                    length = dist + arcs[0]["radius"] * 2
                    self.features.append(SlotFeature(
                        width=arcs[0]["radius"]*2, length=length,
                        axis=arcs[0]["axis"], center=slot_center,
                        depth=depth, through=through, face=arcs[0]["face"]
                    ))
                else:
                    # Arco singolo non classificabile -> opening
                    self.features.append(OpeningFeature(depth=depth, through=through, bottom_plane=arcs[0]["face"]))


        # Raggruppa coppie di slot_half coassiali e alla stessa quota -> SlotFeature
        used_slots = set()
        for i, s1 in enumerate(slot_halves):
            if i in used_slots: continue
            for j, s2 in enumerate(slot_halves):
                if j <= i or j in used_slots: continue
                # Stesso raggio, assi paralleli, stessa profondità
                if abs(s1["radius"] - s2["radius"]) > 0.1: continue
                if abs(np.dot(s1["axis"], s2["axis"])) < 0.99: continue
                c1, c2 = np.array(s1["center"]), np.array(s2["center"])
                dist = np.linalg.norm(c1 - c2)
                if dist < s1["radius"] * 0.5 or dist > 500: continue
                through = not self._cylinder_has_bottom(s1["face"])
                slot_center = tuple((c1 + c2) / 2)
                length = dist + s1["radius"] * 2
                self.features.append(SlotFeature(
                    width=s1["radius"] * 2, length=length,
                    axis=s1["axis"], center=slot_center,
                    depth=s1["depth"], through=through, face=s1["face"]
                ))
                used_slots.add(i); used_slots.add(j); break

        # Cilindri normali: solo countersunk e fillet
        # I fori (cylinder con is_fillet=False) sono gestiti da _find_holes_from_arc_groups
        for i, cyl in normal_cyls:
            found = False
            for j, cone in enumerate(cones):
                if j in used_cones: continue
                if abs(abs(np.dot(cyl["axis"], cone["axis"])) - 1.0) < 1e-5:
                    dist = np.linalg.norm(np.array(cyl["center"]) - np.array(cone["center"]))
                    if dist < (cyl["depth"] + 10.0):
                        self.features.append(CountersunkHoleFeature(
                            diameter=cyl["radius"]*2, axis=cyl["axis"], center=cyl["center"],
                            depth=cyl["depth"], cs_diameter=max(cone["radii"])*2,
                            cs_angle=cone["angle"]*2, face=cyl["face"]))
                        used_cones.add(j); used_cyls.add(i); found = True; break
            if not found and i not in used_cyls:
                if cyl["is_fillet"]:
                    self.features.append(FilletFeature(radius=cyl["radius"], is_concave=True, face=cyl["face"]))
                # HoleFeature per cilindri completi: gestita da _find_holes_from_arc_groups

    def _group_holes(self):
        """Raggruppa HoleFeature con stesso diametro e stessa profondità in HoleGroup."""
        holes = [f for f in self.features if isinstance(f, HoleFeature)]
        if not holes:
            return
        # Rimuove i singoli HoleFeature dalla lista
        self.features = [f for f in self.features if not isinstance(f, HoleFeature)]

        used = set()
        for i, h1 in enumerate(holes):
            if i in used:
                continue
            group = [h1]
            used.add(i)
            for j, h2 in enumerate(holes):
                if j in used:
                    continue
                same_diam = abs(h1.props["diameter"] - h2.props["diameter"]) < 0.1
                same_depth = h2.props["through"] == h1.props["through"] and (
                    h1.props["through"] or abs(h1.props["depth"] - h2.props["depth"]) < 0.5
                )
                if same_diam and same_depth:
                    group.append(h2)
                    used.add(j)
            self.features.append(HoleGroup(
                diameter=h1.props["diameter"],
                depth=h1.props["depth"],
                through=h1.props["through"],
                holes=group,
            ))

    def _find_edge_holes(self):
        """Trova fori sul bordo del pezzo: facce piane verticali con archi circolari
        nel piano XY che insieme coprono 2pi formano un foro."""
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_Circle

        # Raccoglie tutti gli archi circolari con asse parallelo all'utensile
        # che appartengono a facce piane verticali
        # chiave: (radius_rounded, cx_rounded, cy_rounded) -> lista di (edge, angle_span, face_cyl)
        arc_groups: Dict[tuple, list] = {}

        exp_f = TopExp_Explorer(self.model.brep, TopAbs_FACE)
        while exp_f.More():
            face = exp_f.Current()
            face_s = BRepAdaptor_Surface(TopoDS.Face_s(face), True)
            if face_s.GetType() != GeomAbs_Plane:
                exp_f.Next()
                continue
            # Solo facce verticali
            nf_n = np.array([face_s.Plane().Axis().Direction().X(),
                             face_s.Plane().Axis().Direction().Y(),
                             face_s.Plane().Axis().Direction().Z()])
            if abs(np.dot(nf_n, self.working_plane_normal)) > 0.05:
                exp_f.Next()
                continue

            exp_e = TopExp_Explorer(face, TopAbs_EDGE)
            while exp_e.More():
                edge = exp_e.Current()
                try:
                    c = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
                    if c.GetType() != GeomAbs_Circle:
                        exp_e.Next()
                        continue
                    circ = c.Circle()
                    ax_dir = np.array([circ.Axis().Direction().X(),
                                       circ.Axis().Direction().Y(),
                                       circ.Axis().Direction().Z()])
                    # Asse del cerchio parallelo all'utensile
                    if abs(np.dot(ax_dir, self.working_plane_normal)) < 0.99:
                        exp_e.Next()
                        continue
                    radius = circ.Radius()
                    loc = circ.Location()
                    # Proietta il centro sul piano perpendicolare all'asse utensile
                    cx = round(loc.X(), 2)
                    cy = round(loc.Y(), 2)
                    cz = round(loc.Z(), 2)
                    angle_span = abs(c.LastParameter() - c.FirstParameter())
                    key = (round(radius, 2), cx, cy, cz)
                    if key not in arc_groups:
                        arc_groups[key] = []
                    arc_groups[key].append({
                        "angle": angle_span,
                        "edge": edge,
                        "face": face,
                        "radius": radius,
                        "center": np.array([loc.X(), loc.Y(), loc.Z()]),
                    })
                except Exception:
                    pass
                exp_e.Next()
            exp_f.Next()

        # Per ogni gruppo: se la somma degli angoli e' ~2pi -> foro
        for key, arcs in arc_groups.items():
            total_angle = sum(a["angle"] for a in arcs)
            if abs(total_angle - 2 * math.pi) > 0.4:
                continue
            # Trova la faccia cilindrica associata tramite edge condiviso
            radius = arcs[0]["radius"]
            center = arcs[0]["center"]
            cyl_face = None
            for arc in arcs:
                idx = self._edge_face_map.FindIndex(arc["edge"])
                if idx <= 0:
                    continue
                exp_f2 = TopExp_Explorer(self.model.brep, TopAbs_FACE)
                while exp_f2.More():
                    nf2 = exp_f2.Current()
                    nf2_s = BRepAdaptor_Surface(TopoDS.Face_s(nf2), True)
                    if nf2_s.GetType() == GeomAbs_Cylinder:
                        if abs(nf2_s.Cylinder().Radius() - radius) < 0.05:
                            exp_e2 = TopExp_Explorer(nf2, TopAbs_EDGE)
                            while exp_e2.More():
                                if self._edge_face_map.FindIndex(exp_e2.Current()) == idx:
                                    cyl_face = nf2
                                    break
                                exp_e2.Next()
                    if cyl_face:
                        break
                    exp_f2.Next()
                if cyl_face:
                    break

            if cyl_face is None:
                continue

            cyl_s = BRepAdaptor_Surface(TopoDS.Face_s(cyl_face), True)
            axis_dir = np.array([cyl_s.Cylinder().Axis().Direction().X(),
                                  cyl_s.Cylinder().Axis().Direction().Y(),
                                  cyl_s.Cylinder().Axis().Direction().Z()])
            depth = abs(cyl_s.LastVParameter() - cyl_s.FirstVParameter())
            through = not self._cylinder_has_real_bottom(TopoDS.Face_s(cyl_face), cyl_s)
            feat = HoleFeature(
                diameter=radius * 2,
                axis=tuple(axis_dir),
                center=tuple(center),
                depth=depth,
                through=through,
                face=cyl_face
            )
            self.features.append(feat)

    def find_vertical_faces_with_xy_arcs(self) -> List[dict]:
        """Trova archi circolari con asse parallelo all'utensile nel BRep,
        deduplicati per edge. Raggruppa per (radius, cx, cy)."""
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_Circle

        groups: Dict[tuple, dict] = {}
        seen_edge_indices = set()

        exp_f = TopExp_Explorer(self.model.brep, TopAbs_FACE)
        while exp_f.More():
            face = exp_f.Current()
            exp_e = TopExp_Explorer(face, TopAbs_EDGE)
            while exp_e.More():
                edge = exp_e.Current()
                # Deduplicazione tramite indice stabile
                idx = self._edge_face_map.FindIndex(edge)
                if idx > 0 and idx in seen_edge_indices:
                    exp_e.Next()
                    continue
                if idx > 0:
                    seen_edge_indices.add(idx)
                try:
                    c = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
                    if c.GetType() != GeomAbs_Circle:
                        exp_e.Next()
                        continue
                    circ = c.Circle()
                    ax_dir = np.array([circ.Axis().Direction().X(),
                                       circ.Axis().Direction().Y(),
                                       circ.Axis().Direction().Z()])
                    if abs(np.dot(ax_dir, self.working_plane_normal)) < 0.99:
                        exp_e.Next()
                        continue
                    radius = circ.Radius()
                    loc = circ.Location()
                    angle_span = abs(c.LastParameter() - c.FirstParameter())
                    key = (round(radius, 2), round(loc.X(), 2), round(loc.Y(), 2))
                    z = float(np.dot([loc.X(), loc.Y(), loc.Z()], self.working_plane_normal))
                    if abs(z) < 1e-9: z = 0.0
                    z_key = round(z, 1)
                    if key not in groups:
                        groups[key] = {
                            "radius": radius,
                            "center_xy": np.array([loc.X(), loc.Y()]),
                            "faces": [],
                            "circles": {},
                        }
                    # Raccoglie le facce cilindriche adiacenti a questo edge
                    edge_idx = self._edge_face_map.FindIndex(edge)
                    if edge_idx > 0:
                        exp_f2 = TopExp_Explorer(self.model.brep, TopAbs_FACE)
                        while exp_f2.More():
                            nf = exp_f2.Current()
                            nf_s = BRepAdaptor_Surface(TopoDS.Face_s(nf), True)
                            if nf_s.GetType() == GeomAbs_Cylinder:
                                exp_e2 = TopExp_Explorer(nf, TopAbs_EDGE)
                                while exp_e2.More():
                                    if self._edge_face_map.FindIndex(exp_e2.Current()) == edge_idx:
                                        if nf not in groups[key]["faces"]:
                                            groups[key]["faces"].append(nf)
                                        break
                                    exp_e2.Next()
                            exp_f2.Next()
                    groups[key]["circles"][z_key] = groups[key]["circles"].get(z_key, 0.0) + angle_span
                except Exception:
                    pass
                exp_e.Next()
            exp_f.Next()

        result = list(groups.values())
        logger.info(f"find_vertical_faces_with_xy_arcs: {len(result)} gruppi")
        for g in result:
            complete_z = {z: a for z, a in g["circles"].items() if abs(a - 2*math.pi) < 0.4}
            g["complete_z"] = complete_z
            # logger.debug(f"find_vertical_faces_with_xy_arcs: r={g['radius']:.2f} complete_z={list(complete_z.keys())}")
        return result

    def _find_holes_from_arc_groups(self, arc_groups: List[dict]):
        """Converte i gruppi di archi circolari in HoleFeature,
        filtrando per accessibilita' e determinando passante/cieco."""
        for group in arc_groups:
            complete_z = group.get("complete_z", {})
            if not complete_z:
                continue

            radius = group["radius"]
            center_xy = group["center_xy"]

            # Trova la faccia cilindrica con asse parallelo all'utensile
            cyl_face = None
            for f in group["faces"]:
                fs = BRepAdaptor_Surface(TopoDS.Face_s(f), True)
                if fs.GetType() == GeomAbs_Cylinder:
                    ax = fs.Cylinder().Axis().Direction()
                    ax_np = np.array([ax.X(), ax.Y(), ax.Z()])
                    if abs(np.dot(ax_np, self.working_plane_normal)) >= 0.99:
                        cyl_face = f
                        break
            if cyl_face is None:
                continue

            # Scarta superfici esterne (FORWARD)
            if TopoDS.Face_s(cyl_face).Orientation().name == "TopAbs_FORWARD":
                continue

            z_vals = sorted(complete_z.keys())
            depth = abs(max(z_vals) - min(z_vals)) if len(z_vals) >= 2 else abs(self._max_proj_z - min(z_vals))

            # Raccoglie facce tappo candidate
            cap_faces = self._find_cap_faces(center_xy, radius, z_vals)

            # Un foro e' cieco se ha un tappo alla quota inferiore (z_bot)
            # ed e' passante se non ha tappi
            z_bot = min(z_vals)
            tol_z = 0.2
            n = self.working_plane_normal
            caps_at_bot = []
            for cf in cap_faces:
                cf_s = BRepAdaptor_Surface(TopoDS.Face_s(cf), True)
                cf_loc = cf_s.Plane().Axis().Location()
                cf_proj = float(np.dot([cf_loc.X(), cf_loc.Y(), cf_loc.Z()], n))
                if abs(cf_proj) < 1e-9: cf_proj = 0.0
                z_bot_norm = float(z_bot)
                if abs(z_bot_norm) < 1e-9: z_bot_norm = 0.0
                if abs(cf_proj - z_bot_norm) < tol_z:
                    caps_at_bot.append(cf)
            through = len(caps_at_bot) == 0

            # Scarta fori con depth=0 (geometria degenere)
            if depth < 0.1:
                continue

            cyl_s = BRepAdaptor_Surface(TopoDS.Face_s(cyl_face), True)

            # Verifica accessibilita'
            if self._is_cylinder_obstructed(TopoDS.Face_s(cyl_face), cyl_s):
                continue

            z_top = max(z_vals)
            center_3d = (float(center_xy[0]), float(center_xy[1]), float(z_top))
            axis_dir = np.array([cyl_s.Cylinder().Axis().Direction().X(),
                                  cyl_s.Cylinder().Axis().Direction().Y(),
                                  cyl_s.Cylinder().Axis().Direction().Z()])

            feat = HoleFeature(
                diameter=radius * 2,
                axis=tuple(axis_dir),
                center=center_3d,
                depth=depth,
                through=through,
                face=cyl_face
            )
            feat.props["all_faces"] = group["faces"]
            feat.props["from_arc_groups"] = True
            feat.props["cap_faces"] = cap_faces
            self.features.append(feat)

    def _find_cap_faces(self, center_xy: np.ndarray, radius: float, z_vals: list) -> list:
        """Trova facce piane orizzontali il cui bordo esterno (primo wire)
        condivide un edge con la faccia cilindrica del foro."""
        from OCP.TopAbs import TopAbs_WIRE
        n = self.working_plane_normal
        tol_z = 0.2
        tol_r = 0.5
        cap_faces = []

        # Raccoglie gli indici degli edge del bordo superiore/inferiore del cilindro
        cyl_edge_indices = set()
        exp_f = TopExp_Explorer(self.model.brep, TopAbs_FACE)
        while exp_f.More():
            nf = exp_f.Current()
            nf_s = BRepAdaptor_Surface(TopoDS.Face_s(nf), True)
            if nf_s.GetType() == GeomAbs_Cylinder:
                cyl = nf_s.Cylinder()
                if abs(cyl.Radius() - radius) < tol_r:
                    ax = np.array([cyl.Axis().Direction().X(),
                                   cyl.Axis().Direction().Y(),
                                   cyl.Axis().Direction().Z()])
                    if abs(np.dot(ax, n)) >= 0.99:
                        loc = cyl.Axis().Location()
                        dist = math.sqrt((loc.X() - float(center_xy[0]))**2 +
                                         (loc.Y() - float(center_xy[1]))**2)
                        if dist < tol_r:
                            exp_e = TopExp_Explorer(nf, TopAbs_EDGE)
                            while exp_e.More():
                                idx = self._edge_face_map.FindIndex(exp_e.Current())
                                if idx > 0:
                                    cyl_edge_indices.add(idx)
                                exp_e.Next()
            exp_f.Next()

        if not cyl_edge_indices:
            return []

        from OCP.BRepTools import BRepTools
        from OCP.BRepClass import BRepClass_FaceClassifier
        from OCP.TopAbs import TopAbs_IN, TopAbs_ON
        z_bot = min(z_vals)

        # Cerca facce piane orizzontali il cui PRIMO wire condivide un edge col cilindro
        exp_f = TopExp_Explorer(self.model.brep, TopAbs_FACE)
        while exp_f.More():
            nf = exp_f.Current()
            nf_s = BRepAdaptor_Surface(TopoDS.Face_s(nf), True)
            if nf_s.GetType() == GeomAbs_Plane:
                nf_n = np.array([nf_s.Plane().Axis().Direction().X(),
                                 nf_s.Plane().Axis().Direction().Y(),
                                 nf_s.Plane().Axis().Direction().Z()])
                if abs(np.dot(nf_n, n)) >= 0.99:
                    nf_loc = nf_s.Plane().Axis().Location()
                    nf_proj = np.dot([nf_loc.X(), nf_loc.Y(), nf_loc.Z()], n)
                    # Cerca a tutte le quote del foro
                    if any(abs(nf_proj - z) < tol_z for z in z_vals):
                        # Controlla solo il PRIMO wire (bordo esterno)
                        exp_w = TopExp_Explorer(nf, TopAbs_WIRE)
                        if exp_w.More():
                            outer_wire = exp_w.Current()
                            exp_e = TopExp_Explorer(outer_wire, TopAbs_EDGE)
                            shared = False
                            while exp_e.More():
                                idx = self._edge_face_map.FindIndex(exp_e.Current())
                                if idx in cyl_edge_indices:
                                    shared = True
                                    break
                                exp_e.Next()
                            # Fallback geometrico a z_bot: un punto sul bordo del foro cade dentro la faccia
                            if not shared and abs(nf_proj - z_bot) < tol_z:
                                try:
                                    classifier = BRepClass_FaceClassifier()
                                    # Usa un punto leggermente dentro il bordo del foro (a radius*0.5 dal centro)
                                    test_pnt = gp_Pnt(
                                        float(center_xy[0]) + radius * 0.5,
                                        float(center_xy[1]),
                                        nf_proj
                                    )
                                    classifier.Perform(TopoDS.Face_s(nf), test_pnt, 1e-4)
                                    state = classifier.State()
                                    if state == TopAbs_IN or state == TopAbs_ON:
                                        shared = True
                                except Exception:
                                    pass
                            if shared and nf not in cap_faces:
                                # Verifica che un punto interno della faccia
                                # sia dentro il cerchio del foro
                                surf_nf = BRepAdaptor_Surface(TopoDS.Face_s(nf), True)
                                u_mid = (surf_nf.FirstUParameter() + surf_nf.LastUParameter()) / 2
                                v_mid = (surf_nf.FirstVParameter() + surf_nf.LastVParameter()) / 2
                                p = surf_nf.Value(u_mid, v_mid)
                                dx = p.X() - float(center_xy[0])
                                dy = p.Y() - float(center_xy[1])
                                dist_center = math.sqrt(dx*dx + dy*dy)
                                if dist_center <= radius + tol_r:
                                    cap_faces.append(nf)
            exp_f.Next()
        return cap_faces

    def _get_faces(self, shape):
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        faces = []
        while exp.More(): faces.append(exp.Current()); exp.Next()
        return faces
