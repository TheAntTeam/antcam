import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Any, Optional, Tuple, Dict, Union

"""Feature extraction pipeline for BRep models.

The extractor focuses on 2.5D manufacturing-relevant features and combines
surface classification, accessibility checks, and geometric grouping.
"""

import trimesh
import numpy as np

from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_REVERSED, TopAbs_SHAPE
from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCP.GeomAbs import (
    GeomAbs_Plane,
    GeomAbs_Cylinder,
    GeomAbs_Cone,
    GeomAbs_Sphere,
    GeomAbs_Torus,
    GeomAbs_Circle,
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
    """Base feature container.

    Attributes:
        type: Semantic feature type label.
        geometry: Optional OCC geometry associated with the feature.
        props: Arbitrary metadata used by visualization and downstream logic.
    """
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


@dataclass
class RecognitionTolerances:
    """Tolerance bundle for the most common semantic-recognition checks."""

    obstruction_offset: float = 0.01
    ray_intersection: float = 1e-4
    axis_parallel_dot: float = 0.99
    axis_perpendicular_dot: float = 0.05
    axis_in_plane_dot: float = 0.1
    half_cylinder_angle_tolerance: float = 0.4
    max_fillet_radius: float = 15.0
    cone_chamfer_u_span: float = 6.0
    chamfer_dot_min: float = 0.17
    chamfer_dot_max: float = 0.98
    hole_group_diameter: float = 0.1
    hole_group_depth: float = 0.5
    hole_cap_z: float = 0.2
    hole_cap_radius: float = 0.5
    full_circle_angle: float = 0.4
    arc_forward_full_angle_tolerance: float = 0.5
    arc_forward_single_half_angle_tolerance: float = 0.3
    slot_center_distance_min_factor: float = 0.5
    slot_center_distance_max: float = 500.0
    countersink_center_distance_slack: float = 10.0
    min_feature_depth: float = 0.1


# ============================================================
# FEATURE EXTRACTOR
# ============================================================

class FeatureExtractor:
    """Extract manufacturable features from a BRep model.

    The main workflow is orchestrated by :meth:`extract`, which gathers raw
    surface candidates and then builds higher-level composite features.
    """
    def __init__(
        self,
        model,
        working_plane_normal: Tuple[float, ...] = (0.0, 0.0, 1.0),
        tolerances: Optional[RecognitionTolerances] = None,
    ):
        self.model = model
        self.features: List[Feature] = []
        self._bbox = None
        self._edge_to_faces = None
        self.working_plane_normal = np.array(working_plane_normal)
        self.working_plane_normal /= np.linalg.norm(self.working_plane_normal)
        self.tolerances = tolerances or RecognitionTolerances()
        self._max_proj_z = 0.0
        self._min_proj_z = 0.0
        self.diagnostics: Dict[str, Any] = {}
        self._reset_diagnostics()

    def _reset_diagnostics(self) -> None:
        """Reset the structured diagnostics collected during extraction."""
        self.diagnostics = {
            "has_brep": False,
            "skip_reason": None,
            "raw_candidates": {
                "cylinders": 0,
                "cones": 0,
                "arc_groups": 0,
            },
            "pre_group_feature_count": 0,
            "feature_count": 0,
            "feature_types": {},
        }

    def _finalize_diagnostics(self) -> None:
        """Persist a compact extraction summary for logs, tests, and debug tools."""
        feature_types = Counter(getattr(feature, "type", "unknown") for feature in self.features)
        self.diagnostics["feature_count"] = len(self.features)
        self.diagnostics["feature_types"] = dict(sorted(feature_types.items()))

    def extract(self) -> List[Feature]:
        """Run the full feature extraction pipeline.

        Returns:
            List[Feature]: Extracted features. The list can be empty when no
            valid BRep is available (for example STL mesh-only models).
        """
        logger.info(f"Extracting features (Tool Axis: {self.working_plane_normal})")
        self.features = []
        self._arc_groups = []
        self._reset_diagnostics()
        self.diagnostics["has_brep"] = self._has_brep_model()

        if not self._has_brep_model():
            logger.warning("No BRep available: skipping BRep feature extraction")
            self.diagnostics["skip_reason"] = "no_brep"
            self._finalize_diagnostics()
            return self.features

        self._prepare_brep_context()
        cylinders, cones = self._collect_surface_candidates()
        self.diagnostics["raw_candidates"]["cylinders"] = len(cylinders)
        self.diagnostics["raw_candidates"]["cones"] = len(cones)
        self.diagnostics["pre_group_feature_count"] = len(self.features)

        self._group_composite_features(cylinders, cones)
        self._arc_groups = self._extract_arc_group_features()
        self._group_holes()
        self._finalize_diagnostics()
        return self.features

    def _has_brep_model(self) -> bool:
        """Return True when the current model exposes a usable BRep shape."""
        return bool(self.model and hasattr(self.model, "brep") and self.model.brep is not None)

    def _prepare_brep_context(self) -> None:
        """Build the lookup structures required by the BRep detectors."""
        self._calculate_brep_bbox(self.model.brep)
        self._edge_to_faces = self._map_edge_to_faces(self.model.brep)

        # Stable edge-to-face map used by cylindrical bottom checks.
        from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

        self._edge_face_map = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(self.model.brep, TopAbs_EDGE, TopAbs_FACE, self._edge_face_map)

    def _collect_surface_candidates(self) -> Tuple[List[dict], List[dict]]:
        """Scan model faces and collect raw cylindrical and conical candidates."""
        cylinders: List[dict] = []
        cones: List[dict] = []

        for face in self._get_faces(self.model.brep):
            self._collect_face_candidate(face, cylinders, cones)

        return cylinders, cones

    def _collect_face_candidate(self, face, cylinders: List[dict], cones: List[dict]) -> None:
        """Classify one face into raw detector buckets or immediate planar features."""
        topo_face = TopoDS.Face_s(face)
        surf = BRepAdaptor_Surface(topo_face, True)
        stype = surf.GetType()

        if stype == GeomAbs_Cylinder:
            self._collect_cylindrical_candidate(topo_face, surf, cylinders)
            return

        if stype == GeomAbs_Cone:
            self._collect_conical_candidate(topo_face, surf, cones)
            return

        if stype != GeomAbs_Plane:
            return

        self._collect_planar_feature(topo_face, surf)

    def _collect_cylindrical_candidate(self, topo_face, surf, cylinders: List[dict]) -> None:
        """Append one normalized cylindrical candidate when the face is machinable."""
        data = self._analyze_cylindrical_face(topo_face, surf)
        if data and not (data.get("is_fillet") and self._is_cylinder_obstructed(topo_face, surf)):
            cylinders.append(data)

    def _collect_conical_candidate(self, topo_face, surf, cones: List[dict]) -> None:
        """Append one normalized conical candidate when the face matches the tool axis."""
        data = self._analyze_conical_face(topo_face, surf)
        if data:
            cones.append(data)

    def _collect_planar_feature(self, topo_face, surf) -> None:
        """Append an immediate planar or chamfer feature when the face is accessible."""
        if self._is_face_obstructed(topo_face, self.model.brep):
            return

        plane_feature = self._analyze_planar_face_topologicamente(topo_face, surf)
        if plane_feature is None:
            plane_feature = self._analyze_chamfer_face(topo_face, surf)
        if plane_feature:
            self.features.append(plane_feature)

    def _extract_arc_group_features(self) -> List[dict]:
        """Run the arc-group detector branch and append the hole features it yields."""
        arc_groups = self.find_vertical_faces_with_xy_arcs()
        self.diagnostics["raw_candidates"]["arc_groups"] = len(arc_groups)
        self._find_holes_from_arc_groups(arc_groups)
        return arc_groups

    def _is_face_obstructed(self, face, brep) -> bool:
        """Return True if material exists above a face along the tool axis.

        This is a conservative 2.5D accessibility test used to reject faces that
        are not directly machinable from the selected direction.
        """
        surf = BRepAdaptor_Surface(face, True)
        u = (surf.FirstUParameter() + surf.LastUParameter()) / 2
        v = (surf.FirstVParameter() + surf.LastVParameter()) / 2
        pnt = surf.Value(u, v)

        # The offset must clear the BRep tolerance without jumping over nearby geometry.
        eps = self.tolerances.obstruction_offset
        start_pnt = gp_Pnt(pnt.X() + self.working_plane_normal[0]*eps,
                           pnt.Y() + self.working_plane_normal[1]*eps,
                           pnt.Z() + self.working_plane_normal[2]*eps)
        direction = gp_Dir(self.working_plane_normal[0], self.working_plane_normal[1], self.working_plane_normal[2])
        line = gp_Lin(start_pnt, direction)

        inter = BRepIntCurveSurface_Inter()
        inter.Init(brep, line, self.tolerances.ray_intersection)
        # Ignore intersections that are effectively on the source face.
        while inter.More():
            if inter.W() > eps * 10:
                return True
            inter.Next()
        return False

    def _is_cylinder_obstructed(self, face, surf) -> bool:
        """Return True if a cylindrical area is blocked along the tool axis."""
        # Sample the upper rim of the cylinder at the largest tool-axis projection.
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
        eps = self.tolerances.obstruction_offset
        n = self.working_plane_normal
        start = gp_Pnt(best_pnt.X() + n[0]*eps,
                       best_pnt.Y() + n[1]*eps,
                       best_pnt.Z() + n[2]*eps)
        line = gp_Lin(start, gp_Dir(n[0], n[1], n[2]))
        inter = BRepIntCurveSurface_Inter()
        inter.Init(self.model.brep, line, self.tolerances.ray_intersection)
        while inter.More():
            if inter.W() > eps * 10:
                return True
            inter.Next()
        return False

    def _calculate_brep_bbox(self, shape):
        """Compute axis-projected min/max extents used by depth heuristics."""
        bbox = Bnd_Box()
        BRepBndLib.Add_s(shape, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        corners = [np.array([xmin, ymin, zmin]), np.array([xmax, ymin, zmin]), np.array([xmin, ymax, zmin]), np.array([xmax, ymax, zmin]),
                   np.array([xmin, ymin, zmax]), np.array([xmax, ymin, zmax]), np.array([xmin, ymax, zmax]), np.array([xmax, ymax, zmax])]
        self._max_proj_z = max(np.dot(c, self.working_plane_normal) for c in corners)
        self._min_proj_z = min(np.dot(c, self.working_plane_normal) for c in corners)

    def _map_edge_to_faces(self, shape):
        """Build a lightweight edge-to-faces adjacency map."""
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
        """Classify a cylindrical face candidate and return normalized metadata."""
        radius = surf.Cylinder().Radius()
        axis_dir = np.array((surf.Cylinder().Axis().Direction().X(), surf.Cylinder().Axis().Direction().Y(), surf.Cylinder().Axis().Direction().Z()))
        dot_axis = abs(np.dot(axis_dir, self.working_plane_normal))
        u1, u2 = surf.FirstUParameter(), surf.LastUParameter()
        u_range = abs(u2 - u1)
        depth = abs(surf.LastVParameter() - surf.FirstVParameter())
        center = (surf.Cylinder().Axis().Location().X(), surf.Cylinder().Axis().Location().Y(), surf.Cylinder().Axis().Location().Z())

        if dot_axis >= self.tolerances.axis_parallel_dot:
            return self._analyze_axis_aligned_cylindrical_face(face, surf, radius, axis_dir, center, depth, u_range)

        if dot_axis <= self.tolerances.axis_in_plane_dot:
            return self._analyze_in_plane_cylindrical_face(face, radius, axis_dir, center, depth, u_range)

        return None

    def _analyze_axis_aligned_cylindrical_face(
        self,
        face,
        surf,
        radius: float,
        axis_dir: np.ndarray,
        center: Tuple[float, float, float],
        depth: float,
        u_range: float,
    ) -> Optional[dict]:
        """Classify cylinders whose axis is aligned with the selected tool direction."""
        if self._is_full_cylindrical_span(u_range):
            through = not self._cylinder_has_real_bottom(face, surf)
            return self._build_cylindrical_candidate(
                "cylinder",
                radius,
                axis_dir,
                center,
                depth,
                face,
                is_fillet=False,
                through=through,
            )

        if self._is_half_cylindrical_span(u_range):
            return self._build_cylindrical_candidate(
                "arc_forward",
                radius,
                axis_dir,
                center,
                depth,
                face,
                u_range=u_range,
            )

        if self._is_reversed_face(face) and radius < self.tolerances.max_fillet_radius:
            return self._build_cylindrical_candidate(
                "cylinder",
                radius,
                axis_dir,
                center,
                depth,
                face,
                is_fillet=True,
                through=False,
            )

        if self._is_forward_face(face):
            return self._build_cylindrical_candidate(
                "arc_forward",
                radius,
                axis_dir,
                center,
                depth,
                face,
                u_range=u_range,
            )

        return None

    def _analyze_in_plane_cylindrical_face(
        self,
        face,
        radius: float,
        axis_dir: np.ndarray,
        center: Tuple[float, float, float],
        depth: float,
        u_range: float,
    ) -> Optional[dict]:
        """Classify half-cylinders whose axis lies in the working plane."""
        if self._is_half_cylindrical_span(u_range) and self._is_reversed_face(face):
            return self._build_cylindrical_candidate(
                "slot_half",
                radius,
                axis_dir,
                center,
                depth,
                face,
            )
        return None

    def _build_cylindrical_candidate(
        self,
        candidate_type: str,
        radius: float,
        axis_dir: np.ndarray,
        center: Tuple[float, float, float],
        depth: float,
        face,
        **extra_props,
    ) -> dict:
        """Normalize the shared metadata emitted by the cylindrical detectors."""
        candidate = {
            "type": candidate_type,
            "radius": radius,
            "axis": tuple(axis_dir),
            "center": center,
            "depth": depth,
            "face": face,
        }
        candidate.update(extra_props)
        return candidate

    def _is_full_cylindrical_span(self, u_range: float) -> bool:
        """Return True when the cylindrical face covers a full revolution."""
        return u_range >= (2 * math.pi - 0.2)

    def _is_half_cylindrical_span(self, u_range: float) -> bool:
        """Return True when the cylindrical face spans roughly half a revolution."""
        tolerance = self.tolerances.half_cylinder_angle_tolerance
        return math.pi - tolerance <= u_range <= math.pi + tolerance

    def _is_reversed_face(self, face) -> bool:
        """Return True when OCC marks a face as inward-facing for cavity detection."""
        return face.Orientation() == TopAbs_REVERSED

    def _is_forward_face(self, face) -> bool:
        """Return True when OCC marks a face as forward-oriented."""
        return getattr(face.Orientation(), "name", "") == "TopAbs_FORWARD"

    def _get_face_surface(self, face):
        """Return the OCC surface adaptor for a face."""
        return BRepAdaptor_Surface(self._coerce_face(face), True)

    def _coerce_face(self, face):
        """Return a TopoDS face when possible, while tolerating simple test doubles."""
        try:
            return TopoDS.Face_s(face)
        except TypeError:
            return face

    def _cylinder_has_real_bottom(self, face, surf) -> bool:
        """Heuristic blind-hole check based on adjacent horizontal cap faces."""
        _ = face
        proj_bot, proj_top = self._get_cylindrical_end_projections(surf)
        bottom_caps, top_caps = self._count_horizontal_cylinder_caps(proj_bot, proj_top)
        return self._has_internal_cylinder_bottom(bottom_caps, top_caps, proj_bot)

    def _get_cylindrical_end_projections(self, surf) -> Tuple[float, float]:
        """Project the two cylinder rim samples onto the tool axis."""
        v1, v2 = surf.FirstVParameter(), surf.LastVParameter()
        u_mid = (surf.FirstUParameter() + surf.LastUParameter()) / 2
        p_v1 = surf.Value(u_mid, v1)
        p_v2 = surf.Value(u_mid, v2)
        proj_v1 = np.dot([p_v1.X(), p_v1.Y(), p_v1.Z()], self.working_plane_normal)
        proj_v2 = np.dot([p_v2.X(), p_v2.Y(), p_v2.Z()], self.working_plane_normal)
        return min(proj_v1, proj_v2), max(proj_v1, proj_v2)

    def _count_horizontal_cylinder_caps(self, proj_bot: float, proj_top: float) -> Tuple[int, int]:
        """Count horizontal planar faces aligned with the cylinder bottom and top projections."""
        bottom_caps = 0
        top_caps = 0

        for candidate_face in self._get_faces(self.model.brep):
            position = self._classify_cylinder_cap_surface(
                self._get_face_surface(candidate_face),
                proj_bot,
                proj_top,
            )
            if position == "bottom":
                bottom_caps += 1
            elif position == "top":
                top_caps += 1

        return bottom_caps, top_caps

    def _classify_cylinder_cap_surface(self, face_surface, proj_bot: float, proj_top: float) -> Optional[str]:
        """Classify a planar face as a candidate bottom or top cylinder cap."""
        if face_surface.GetType() != GeomAbs_Plane:
            return None
        if not self._is_horizontal_planar_face(self._get_planar_face_normal(face_surface)):
            return None

        face_proj = self._project_planar_face_origin(face_surface)
        cap_tol = 0.1
        if abs(face_proj - proj_bot) < cap_tol and abs(face_proj - self._max_proj_z) >= cap_tol:
            return "bottom"
        if abs(face_proj - proj_top) < cap_tol and abs(face_proj - self._max_proj_z) >= cap_tol:
            return "top"
        return None

    def _has_internal_cylinder_bottom(self, bottom_caps: int, top_caps: int, proj_bot: float) -> bool:
        """Return True when the cylinder has an internal bottom cap but no top cap."""
        return bottom_caps > 0 and top_caps == 0 and abs(proj_bot - self._min_proj_z) > 0.1

    def _stable_neighbors(self, edge_idx: int):
        """Non usato - mantenuto per compatibilità."""
        return []

    def _cylinder_has_bottom(self, face) -> bool:
        """Return True when a cylinder has an adjacent horizontal floor face."""
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
        """Return True if a planar cavity behaves like a closed-bottom pocket."""
        _ = proj_z
        for neighbor in self._iter_adjacent_faces(face):
            neighbor_surface = self._get_face_surface(neighbor)
            if not self._is_vertical_planar_surface(neighbor_surface):
                continue
            if self._face_has_open_boundary(neighbor):
                return False
        return True  # tutte le pareti chiuse -> cieca

    def _iter_adjacent_faces(self, face):
        """Yield unique faces that share at least one edge with the given face."""
        seen = set()
        exp = TopExp_Explorer(face, TopAbs_EDGE)
        while exp.More():
            edge = exp.Current()
            h = hash(edge) if hasattr(edge, "__hash__") else edge.this
            for neighbor in self._edge_to_faces.get(h, []):
                if neighbor == face:
                    continue
                neighbor_key = hash(neighbor) if hasattr(neighbor, "__hash__") else neighbor.this
                if neighbor_key in seen:
                    continue
                seen.add(neighbor_key)
                yield neighbor
            exp.Next()

    def _is_vertical_planar_surface(self, surf) -> bool:
        """Return True when a surface is a planar wall orthogonal to the tool axis."""
        if surf.GetType() != GeomAbs_Plane:
            return False
        normal = self._get_planar_face_normal(surf)
        return bool(abs(np.dot(normal, self.working_plane_normal)) <= self.tolerances.axis_perpendicular_dot)

    def _face_has_open_boundary(self, face) -> bool:
        """Return True when at least one edge of the face is not shared by two faces."""
        exp = TopExp_Explorer(face, TopAbs_EDGE)
        while exp.More():
            edge = exp.Current()
            h = hash(edge) if hasattr(edge, "__hash__") else edge.this
            if len(self._edge_to_faces.get(h, [])) < 2:
                return True
            exp.Next()
        return False


    def _analyze_conical_face(self, face, surf) -> Optional[dict]:
        """Classify a conical face and return normalized cone metadata."""
        cone = surf.Cone()
        axis_dir = np.array((cone.Axis().Direction().X(), cone.Axis().Direction().Y(), cone.Axis().Direction().Z()))
        radii = self._get_cone_end_radii(cone, surf)
        opening_axis = self._get_cone_opening_axis(axis_dir, radii)

        if not self._cone_opens_toward_tool(opening_axis):
            return None

        return self._build_conical_candidate(face, cone, surf, opening_axis, radii)

    def _get_cone_end_radii(self, cone, surf) -> Tuple[float, float]:
        """Return the cone radii at the two V-parameter extremes."""
        v1, v2 = surf.FirstVParameter(), surf.LastVParameter()
        tangent = math.tan(cone.SemiAngle())
        r1 = cone.RefRadius() + v1 * tangent
        r2 = cone.RefRadius() + v2 * tangent
        return r1, r2

    def _get_cone_opening_axis(self, axis_dir: np.ndarray, radii: Tuple[float, float]) -> np.ndarray:
        """Normalize the cone axis so it points toward the widening opening."""
        if radii[1] < radii[0]:
            return -axis_dir
        return axis_dir

    def _cone_opens_toward_tool(self, axis_dir: np.ndarray) -> bool:
        """Return True when the cone opening is aligned with the tool axis."""
        return bool(np.dot(axis_dir, self.working_plane_normal) >= self.tolerances.axis_parallel_dot)

    def _build_conical_candidate(self, face, cone, surf, axis_dir: np.ndarray, radii: Tuple[float, float]) -> dict:
        """Normalize the cone metadata emitted by the conical detector branch."""
        u_range, _v_range = self._get_surface_param_spans(surf)
        return {
            "type": "cone",
            "angle": math.degrees(cone.SemiAngle()),
            "is_chamfer": u_range < self.tolerances.cone_chamfer_u_span,
            "radii": radii,
            "axis": tuple(axis_dir),
            "center": (
                cone.Axis().Location().X(),
                cone.Axis().Location().Y(),
                cone.Axis().Location().Z(),
            ),
            "face": face,
        }

    def _analyze_planar_face_topologicamente(self, face, surf) -> Optional[Feature]:
        """Classify planar faces into step/pocket/opening candidates."""
        normal = self._get_planar_face_normal(surf)
        if not self._is_horizontal_planar_face(normal):
            return None

        proj_z = self._project_planar_face_origin(surf)
        open_edges = self._count_open_planar_edges(face)
        return self._build_horizontal_planar_feature(face, proj_z, open_edges, normal)

    def _get_planar_face_normal(self, surf) -> np.ndarray:
        """Return the plane normal used by planar and chamfer classification."""
        axis = surf.Plane().Axis()
        return np.array((axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z()))

    def _project_planar_face_origin(self, surf) -> float:
        """Project the plane origin along the tool direction."""
        location = surf.Plane().Axis().Location()
        return np.dot(
            np.array((location.X(), location.Y(), location.Z())),
            self.working_plane_normal,
        )

    def _is_horizontal_planar_face(self, normal: np.ndarray) -> bool:
        """Return True when a planar face is horizontal relative to the tool axis."""
        return bool(abs(np.dot(normal, self.working_plane_normal)) >= self.tolerances.axis_parallel_dot)

    def _count_open_planar_edges(self, face) -> int:
        """Count boundary edges that behave like an opening on a horizontal planar face."""
        open_edges = 0
        exp = TopExp_Explorer(face, TopAbs_EDGE)
        while exp.More():
            if self._is_open_planar_edge(face, exp.Current()):
                open_edges += 1
            exp.Next()
        return open_edges

    def _is_open_planar_edge(self, face, edge) -> bool:
        """Return True when a planar face edge is not bounded by a vertical wall."""
        h = hash(edge) if hasattr(edge, "__hash__") else edge.this
        neighbors = self._edge_to_faces.get(h, [])
        if len(neighbors) < 2:
            return True
        return not any(self._is_planar_wall_neighbor(face, neighbor) for neighbor in neighbors)

    def _is_planar_wall_neighbor(self, face, neighbor) -> bool:
        """Return True when a neighboring face closes a planar cavity edge."""
        if neighbor == face:
            return False

        neighbor_surface = BRepAdaptor_Surface(TopoDS.Face_s(neighbor), True)
        if neighbor_surface.GetType() == GeomAbs_Plane:
            neighbor_normal = np.array((
                neighbor_surface.Plane().Axis().Direction().X(),
                neighbor_surface.Plane().Axis().Direction().Y(),
                neighbor_surface.Plane().Axis().Direction().Z(),
            ))
            return abs(np.dot(neighbor_normal, self.working_plane_normal)) < self.tolerances.axis_perpendicular_dot

        return neighbor_surface.GetType() in [GeomAbs_Cylinder, GeomAbs_Cone]

    def _build_horizontal_planar_feature(
        self,
        face,
        proj_z: float,
        open_edges: int,
        normal: np.ndarray,
    ) -> Feature:
        """Convert one horizontal planar candidate into step, opening, or pocket output."""
        depth = self._max_proj_z - proj_z
        if open_edges > 0:
            return StepFeature(depth=depth, bottom_plane=face, open_edges_count=open_edges)

        through = not self._plane_has_floor(face, proj_z)
        if through:
            return OpeningFeature(depth=depth, through=True, bottom_plane=face)

        return PocketFeature(depth=depth, bottom_plane=face, normal=tuple(normal))

    def _analyze_chamfer_face(self, face, surf) -> Optional[Feature]:
        """Detect planar chamfer candidates from orientation and adjacency rules."""
        normal = self._get_planar_face_normal(surf)
        dot = abs(np.dot(normal, self.working_plane_normal))
        if not self._is_chamfer_angle_candidate(dot):
            return None

        has_horizontal_neighbor, has_vertical_neighbor = self._collect_chamfer_neighbor_flags(face)
        if not (has_horizontal_neighbor and has_vertical_neighbor):
            return None

        return self._build_chamfer_feature(face, surf, dot)

    def _is_chamfer_angle_candidate(self, dot: float) -> bool:
        """Return True when a planar face orientation falls in the chamfer band."""
        return self.tolerances.chamfer_dot_min <= dot <= self.tolerances.chamfer_dot_max

    def _collect_chamfer_neighbor_flags(self, face) -> Tuple[bool, bool]:
        """Track whether a chamfer candidate touches both horizontal and vertical planes."""
        has_horizontal_neighbor = False
        has_vertical_neighbor = False
        exp = TopExp_Explorer(face, TopAbs_EDGE)
        while exp.More():
            edge = exp.Current()
            h = hash(edge) if hasattr(edge, "__hash__") else edge.this
            for neighbor in self._edge_to_faces.get(h, []):
                has_horizontal_neighbor, has_vertical_neighbor = self._update_chamfer_neighbor_flags(
                    face,
                    neighbor,
                    has_horizontal_neighbor,
                    has_vertical_neighbor,
                )
            exp.Next()
        return has_horizontal_neighbor, has_vertical_neighbor

    def _update_chamfer_neighbor_flags(
        self,
        face,
        neighbor,
        has_horizontal_neighbor: bool,
        has_vertical_neighbor: bool,
    ) -> Tuple[bool, bool]:
        """Update chamfer adjacency flags from one neighboring planar face."""
        if neighbor == face:
            return has_horizontal_neighbor, has_vertical_neighbor

        neighbor_surface = BRepAdaptor_Surface(TopoDS.Face_s(neighbor), True)
        if neighbor_surface.GetType() != GeomAbs_Plane:
            return has_horizontal_neighbor, has_vertical_neighbor

        neighbor_dot = abs(np.dot(self._get_planar_face_normal(neighbor_surface), self.working_plane_normal))
        if neighbor_dot >= self.tolerances.axis_parallel_dot:
            has_horizontal_neighbor = True
        elif neighbor_dot <= self.tolerances.axis_perpendicular_dot:
            has_vertical_neighbor = True

        return has_horizontal_neighbor, has_vertical_neighbor

    def _build_chamfer_feature(self, face, surf, dot: float) -> ChamferFeature:
        """Create the final chamfer feature from a validated planar candidate."""
        angle_deg = math.degrees(math.acos(np.clip(dot, 0.0, 1.0)))
        u_range, v_range = self._get_surface_param_spans(surf)
        width = min(v_range, u_range)
        return ChamferFeature(angle=angle_deg, width=width, face=face)

    def _get_surface_param_spans(self, surf) -> Tuple[float, float]:
        """Return absolute U/V parameter spans for one OCC surface adaptor."""
        return (
            abs(surf.LastUParameter() - surf.FirstUParameter()),
            abs(surf.LastVParameter() - surf.FirstVParameter()),
        )

    def _group_composite_features(self, cylinders, cones):
        """Build composite features (slots, countersinks, fillets) from raw candidates."""
        slot_halves, arc_forwards, normal_cyls = self._partition_cylindrical_candidates(cylinders)
        self._append_slot_features_from_arc_forwards(arc_forwards)
        self._append_slot_features_from_slot_halves(slot_halves)
        self._append_countersinks_and_fillets(normal_cyls, cones)

    def _partition_cylindrical_candidates(self, cylinders):
        """Split cylindrical candidates into the detector branches that consume them."""
        slot_halves = []
        arc_forwards = []
        normal_cyls = []

        for index, candidate in enumerate(cylinders):
            candidate_type = candidate.get("type", "cylinder")
            if candidate_type == "slot_half":
                slot_halves.append(candidate)
            elif candidate_type == "arc_forward":
                arc_forwards.append(candidate)
            elif candidate_type == "cylinder":
                normal_cyls.append((index, candidate))

        return slot_halves, arc_forwards, normal_cyls

    def _append_slot_features_from_arc_forwards(self, arc_forwards) -> None:
        """Build slot/opening features from grouped forward cylindrical arcs."""
        for arcs in self._group_arc_forward_candidates(arc_forwards).values():
            self._append_arc_forward_group_feature(arcs)

    def _group_arc_forward_candidates(self, arc_forwards) -> Dict[tuple, List[dict]]:
        """Group forward cylindrical arcs by radius and axis before semantic classification."""
        arc_groups: Dict[tuple, List[dict]] = {}
        for arc in arc_forwards:
            matched_key = None
            for key in arc_groups:
                key_radius, key_axis, _key_center = key
                if abs(arc["radius"] - key_radius) > 0.1:
                    continue
                if abs(np.dot(arc["axis"], key_axis)) < self.tolerances.axis_parallel_dot:
                    continue
                arc_groups[key].append(arc)
                matched_key = key
                break
            if matched_key is None:
                matched_key = (round(arc["radius"], 2), arc["axis"], arc["center"])
                arc_groups[matched_key] = [arc]

        return arc_groups

    def _append_arc_forward_group_feature(self, arcs: List[dict]) -> None:
        """Convert one grouped set of forward arcs into a slot or opening when possible."""
        total_u = sum(arc["u_range"] for arc in arcs)
        depth = arcs[0]["depth"]
        through = not self._cylinder_has_bottom(arcs[0]["face"])
        centers = [np.array(arc["center"]) for arc in arcs]

        # Full circles and single semicircles are routed to arc-group hole classification.
        if (
            abs(total_u - 2 * math.pi) < self.tolerances.arc_forward_full_angle_tolerance
            or (
                len(arcs) == 1
                and abs(total_u - math.pi) < self.tolerances.arc_forward_single_half_angle_tolerance
            )
        ):
            return

        if len(centers) >= 2:
            c1, c2 = centers[0], centers[-1]
            delta = c2 - c1
            dist = np.linalg.norm(delta)
            if dist <= 1e-9:
                slot_axis = arcs[0]["axis"]
            else:
                slot_axis = tuple(delta / dist)
            slot_center = tuple((c1 + c2) / 2)
            length = dist + arcs[0]["radius"] * 2
            self.features.append(SlotFeature(
                width=arcs[0]["radius"] * 2,
                length=length,
                axis=slot_axis,
                center=slot_center,
                depth=depth,
                through=through,
                face=arcs[0]["face"],
            ))
            return

        self.features.append(OpeningFeature(depth=depth, through=through, bottom_plane=arcs[0]["face"]))

    def _append_slot_features_from_slot_halves(self, slot_halves) -> None:
        """Pair compatible half-cylinders into slot features."""
        used_slots = set()
        for i, s1 in enumerate(slot_halves):
            if i in used_slots:
                continue
            for j, s2 in enumerate(slot_halves):
                if j <= i or j in used_slots:
                    continue
                if abs(s1["radius"] - s2["radius"]) > 0.1:
                    continue
                if abs(np.dot(s1["axis"], s2["axis"])) < self.tolerances.axis_parallel_dot:
                    continue
                c1, c2 = np.array(s1["center"]), np.array(s2["center"])
                dist = np.linalg.norm(c1 - c2)
                if (
                    dist < s1["radius"] * self.tolerances.slot_center_distance_min_factor
                    or dist > self.tolerances.slot_center_distance_max
                ):
                    continue
                through = not self._cylinder_has_bottom(s1["face"])
                slot_center = tuple((c1 + c2) / 2)
                length = dist + s1["radius"] * 2
                self.features.append(SlotFeature(
                    width=s1["radius"] * 2,
                    length=length,
                    axis=s1["axis"],
                    center=slot_center,
                    depth=s1["depth"],
                    through=through,
                    face=s1["face"],
                ))
                used_slots.add(i)
                used_slots.add(j)
                break

    def _append_countersinks_and_fillets(self, normal_cyls, cones) -> None:
        """Combine coaxial cylinder/cone pairs and emit remaining fillets."""
        used_cones = set()
        used_cyls = set()

        for index, cyl in normal_cyls:
            found_countersink = False
            for cone_index, cone in enumerate(cones):
                if cone_index in used_cones:
                    continue
                if abs(abs(np.dot(cyl["axis"], cone["axis"])) - 1.0) >= 1e-5:
                    continue
                dist = np.linalg.norm(np.array(cyl["center"]) - np.array(cone["center"]))
                if dist >= (cyl["depth"] + self.tolerances.countersink_center_distance_slack):
                    continue
                self.features.append(CountersunkHoleFeature(
                    diameter=cyl["radius"] * 2,
                    axis=cyl["axis"],
                    center=cyl["center"],
                    depth=cyl["depth"],
                    cs_diameter=max(cone["radii"]) * 2,
                    cs_angle=cone["angle"] * 2,
                    face=cyl["face"],
                ))
                used_cones.add(cone_index)
                used_cyls.add(index)
                found_countersink = True
                break

            if not found_countersink and index not in used_cyls and cyl.get("is_fillet"):
                self.features.append(FilletFeature(radius=cyl["radius"], is_concave=True, face=cyl["face"]))

    def _group_holes(self):
        """Group compatible HoleFeature items into HoleGroup aggregates."""
        holes = [f for f in self.features if isinstance(f, HoleFeature)]
        if not holes:
            return
        # Remove raw holes once we start building grouped semantic features.
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
                same_diam = abs(h1.props["diameter"] - h2.props["diameter"]) < self.tolerances.hole_group_diameter
                same_depth = h2.props["through"] == h1.props["through"] and (
                    h1.props["through"] or abs(h1.props["depth"] - h2.props["depth"]) < self.tolerances.hole_group_depth
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
        """Detect edge holes using circular-arc accumulation on vertical planes."""
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
            if abs(total_angle - 2 * math.pi) > self.tolerances.full_circle_angle:
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
        """Find circular arcs aligned with tool axis and group them by XY center/radius."""
        if not self.model or not hasattr(self.model, 'brep') or self.model.brep is None:
            logger.warning("No BRep available: skipping vertical arc grouping")
            return []

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
                    if abs(np.dot(ax_dir, self.working_plane_normal)) < self.tolerances.axis_parallel_dot:
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
            complete_z = {
                z: a
                for z, a in g["circles"].items()
                if abs(a - 2 * math.pi) < self.tolerances.full_circle_angle
            }
            g["complete_z"] = complete_z
        return result

    def _find_holes_from_arc_groups(self, arc_groups: List[dict]):
        """Convert grouped arcs into HoleFeature items with blind/through classification."""
        for group in arc_groups:
            feature = self._build_hole_feature_from_arc_group(group)
            if feature is not None:
                self.features.append(feature)

    def _build_hole_feature_from_arc_group(self, group: dict) -> Optional[HoleFeature]:
        """Build one hole feature from a grouped set of complete circular arcs."""
        complete_z = group.get("complete_z", {})
        if not complete_z:
            return None

        cyl_face = self._find_tool_aligned_group_cylinder_face(group.get("faces", []))
        if cyl_face is None:
            return None

        topo_cyl_face = self._coerce_face(cyl_face)
        if self._is_forward_face(topo_cyl_face):
            return None

        z_vals = self._get_arc_group_z_levels(complete_z)
        depth = self._get_arc_group_hole_depth(z_vals)
        if depth < self.tolerances.min_feature_depth:
            return None

        cyl_surface = self._get_face_surface(cyl_face)
        if self._is_cylinder_obstructed(topo_cyl_face, cyl_surface):
            return None

        radius = group["radius"]
        center_xy = group["center_xy"]
        cap_faces = self._find_cap_faces(center_xy, radius, z_vals)
        through = self._is_arc_group_hole_through(cap_faces, z_vals)

        return self._build_arc_group_hole_feature(
            group=group,
            cyl_face=cyl_face,
            cyl_surface=cyl_surface,
            depth=depth,
            through=through,
            cap_faces=cap_faces,
            z_vals=z_vals,
        )

    def _find_tool_aligned_group_cylinder_face(self, faces) -> Optional[Any]:
        """Return the first cylindrical face in a group whose axis is tool-aligned."""
        for face in faces:
            face_surface = self._get_face_surface(face)
            if face_surface.GetType() != GeomAbs_Cylinder:
                continue
            axis = face_surface.Cylinder().Axis().Direction()
            axis_np = np.array([axis.X(), axis.Y(), axis.Z()])
            if abs(np.dot(axis_np, self.working_plane_normal)) >= self.tolerances.axis_parallel_dot:
                return face
        return None

    def _get_arc_group_z_levels(self, complete_z: dict) -> List[float]:
        """Return the sorted complete-circle levels extracted from one arc group."""
        return sorted(complete_z.keys())

    def _get_arc_group_hole_depth(self, z_vals: List[float]) -> float:
        """Compute arc-group hole depth from complete-circle Z levels."""
        if len(z_vals) >= 2:
            return abs(z_vals[-1] - z_vals[0])
        return abs(self._max_proj_z - z_vals[0])

    def _is_arc_group_hole_through(self, cap_faces: List[Any], z_vals: List[float]) -> bool:
        """Return True when no cap face closes the bottom of the arc-group hole."""
        return len(self._get_cap_faces_at_level(cap_faces, min(z_vals))) == 0

    def _get_cap_faces_at_level(self, cap_faces: List[Any], z_level: float) -> List[Any]:
        """Return cap faces whose projected level matches the requested Z value."""
        matching_faces = []
        normalized_level = self._normalize_projection_value(float(z_level))
        for cap_face in cap_faces:
            cap_proj = self._normalize_projection_value(
                self._project_planar_face_origin(self._get_face_surface(cap_face))
            )
            if abs(cap_proj - normalized_level) < self.tolerances.hole_cap_z:
                matching_faces.append(cap_face)
        return matching_faces

    def _build_arc_group_hole_feature(
        self,
        group: dict,
        cyl_face,
        cyl_surface,
        depth: float,
        through: bool,
        cap_faces: List[Any],
        z_vals: List[float],
    ) -> HoleFeature:
        """Create the final hole feature emitted by the arc-group detector branch."""
        center_xy = group["center_xy"]
        z_top = max(z_vals)
        center_3d = (float(center_xy[0]), float(center_xy[1]), float(z_top))
        axis = cyl_surface.Cylinder().Axis().Direction()
        axis_dir = np.array([axis.X(), axis.Y(), axis.Z()])

        feature = HoleFeature(
            diameter=group["radius"] * 2,
            axis=tuple(axis_dir),
            center=center_3d,
            depth=depth,
            through=through,
            face=cyl_face,
        )
        feature.props["all_faces"] = group["faces"]
        feature.props["from_arc_groups"] = True
        feature.props["cap_faces"] = cap_faces
        return feature

    def _find_cap_faces(self, center_xy: np.ndarray, radius: float, z_vals: list) -> list:
        """Find candidate planar cap faces associated with a cylindrical hole wall."""
        cyl_edge_indices = self._collect_hole_wall_edge_indices(center_xy, radius)
        if not cyl_edge_indices:
            return []

        z_bot = min(z_vals)
        cap_faces = []
        for face in self._get_faces(self.model.brep):
            face_surface = self._get_face_surface(face)
            if not self._is_planar_cap_candidate(face_surface, z_vals):
                continue
            if not self._cap_face_is_linked_to_hole(face, face_surface, center_xy, radius, z_bot, cyl_edge_indices):
                continue
            if self._cap_face_within_hole_footprint(face, center_xy, radius):
                cap_faces.append(face)
        return cap_faces

    def _collect_hole_wall_edge_indices(self, center_xy: np.ndarray, radius: float) -> set:
        """Gather stable edge indices from cylindrical walls that match one hole candidate."""
        edge_indices = set()
        for face in self._get_faces(self.model.brep):
            face_surface = self._get_face_surface(face)
            if not self._is_matching_hole_wall_surface(face_surface, center_xy, radius):
                continue
            self._collect_face_edge_indices(face, edge_indices)
        return edge_indices

    def _is_matching_hole_wall_surface(self, face_surface, center_xy: np.ndarray, radius: float) -> bool:
        """Return True when a cylindrical face looks like the wall of the target hole."""
        if face_surface.GetType() != GeomAbs_Cylinder:
            return False

        cylinder = face_surface.Cylinder()
        if abs(cylinder.Radius() - radius) >= self.tolerances.hole_cap_radius:
            return False

        axis = np.array([
            cylinder.Axis().Direction().X(),
            cylinder.Axis().Direction().Y(),
            cylinder.Axis().Direction().Z(),
        ])
        if abs(np.dot(axis, self.working_plane_normal)) < self.tolerances.axis_parallel_dot:
            return False

        location = cylinder.Axis().Location()
        distance_xy = math.sqrt(
            (location.X() - float(center_xy[0]))**2 +
            (location.Y() - float(center_xy[1]))**2
        )
        return bool(distance_xy < self.tolerances.hole_cap_radius)

    def _collect_face_edge_indices(self, face, edge_indices: set) -> None:
        """Add all indexed edges from one face into the provided set."""
        exp = TopExp_Explorer(face, TopAbs_EDGE)
        while exp.More():
            idx = self._edge_face_map.FindIndex(exp.Current())
            if idx > 0:
                edge_indices.add(idx)
            exp.Next()

    def _is_planar_cap_candidate(self, face_surface, z_vals: List[float]) -> bool:
        """Return True when a face is a horizontal planar candidate at one hole level."""
        if face_surface.GetType() != GeomAbs_Plane:
            return False
        if not self._is_horizontal_planar_face(self._get_planar_face_normal(face_surface)):
            return False

        face_proj = self._project_planar_face_origin(face_surface)
        return any(self._matches_hole_level(face_proj, z) for z in z_vals)

    def _matches_hole_level(self, face_proj: float, z_level: float) -> bool:
        """Return True when a projected face level matches a known hole level."""
        return bool(
            abs(
                self._normalize_projection_value(face_proj)
                - self._normalize_projection_value(z_level)
            ) < self.tolerances.hole_cap_z
        )

    def _normalize_projection_value(self, value: float) -> float:
        """Collapse numerical noise around zero for projected tool-axis values."""
        if abs(value) < 1e-9:
            return 0.0
        return float(value)

    def _cap_face_is_linked_to_hole(
        self,
        face,
        face_surface,
        center_xy: np.ndarray,
        radius: float,
        z_bot: float,
        cyl_edge_indices: set,
    ) -> bool:
        """Return True when a planar face is topologically or geometrically linked to the hole."""
        if self._cap_face_shares_cylinder_edge(face, cyl_edge_indices):
            return True

        face_proj = self._project_planar_face_origin(face_surface)
        if not self._matches_hole_level(face_proj, z_bot):
            return False

        return self._cap_face_passes_bottom_interior_test(face, face_proj, center_xy, radius)

    def _cap_face_shares_cylinder_edge(self, face, cyl_edge_indices: set) -> bool:
        """Return True when the outer wire of a planar cap shares topology with the hole wall."""
        from OCP.TopAbs import TopAbs_WIRE

        exp_w = TopExp_Explorer(face, TopAbs_WIRE)
        if not exp_w.More():
            return False

        outer_wire = exp_w.Current()
        exp_e = TopExp_Explorer(outer_wire, TopAbs_EDGE)
        while exp_e.More():
            idx = self._edge_face_map.FindIndex(exp_e.Current())
            if idx in cyl_edge_indices:
                return True
            exp_e.Next()
        return False

    def _cap_face_passes_bottom_interior_test(
        self,
        face,
        face_proj: float,
        center_xy: np.ndarray,
        radius: float,
    ) -> bool:
        """Use a geometric fallback at the bottom hole level when shared topology is missing."""
        from OCP.BRepClass import BRepClass_FaceClassifier
        from OCP.TopAbs import TopAbs_IN, TopAbs_ON

        try:
            classifier = BRepClass_FaceClassifier()
            test_point = gp_Pnt(
                float(center_xy[0]) + radius * 0.5,
                float(center_xy[1]),
                face_proj,
            )
            classifier.Perform(TopoDS.Face_s(face), test_point, self.tolerances.ray_intersection)
            state = classifier.State()
            return state == TopAbs_IN or state == TopAbs_ON
        except Exception:
            return False

    def _cap_face_within_hole_footprint(self, face, center_xy: np.ndarray, radius: float) -> bool:
        """Return True when a candidate cap sits within the XY footprint of the hole."""
        face_surface = self._get_face_surface(face)
        u_mid = (face_surface.FirstUParameter() + face_surface.LastUParameter()) / 2
        v_mid = (face_surface.FirstVParameter() + face_surface.LastVParameter()) / 2
        point = face_surface.Value(u_mid, v_mid)
        dx = point.X() - float(center_xy[0])
        dy = point.Y() - float(center_xy[1])
        distance_xy = math.sqrt(dx * dx + dy * dy)
        return bool(distance_xy <= radius + self.tolerances.hole_cap_radius)

    def _get_faces(self, shape):
        """Return all TopoDS faces from a shape."""
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        faces = []
        while exp.More(): faces.append(exp.Current()); exp.Next()
        return faces
