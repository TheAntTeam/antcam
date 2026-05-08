import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

"""Face indexing and selection heuristics for interactive feature editing.

This module provides a lightweight service used by the viewer to:
- build stable in-session face candidates,
- filter candidates by top accessibility,
- cycle candidates for user navigation,
- validate multi-face manual hole assignments.
"""

import numpy as np

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus
from OCP.GeomLProp import GeomLProp_SLProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

logger = logging.getLogger("antcam")


@dataclass
class FaceCandidate:
    """Viewer-facing metadata for a selectable BRep face.

    Attributes:
        face_id: In-session integer identifier.
        face: OCC face shape handle.
        center: Approximate face center for geometric heuristics.
        surface_type: Simplified surface class name.
        accessible_from_top: True if considered reachable along tool axis.
        neighbors: Adjacent face ids (shared-edge topology).
        compatible_features: Manual feature types allowed on this face.
    """
    face_id: int
    face: Any
    center: Tuple[float, float, float]
    surface_type: str
    accessible_from_top: bool
    neighbors: List[int] = field(default_factory=list)
    compatible_features: List[str] = field(default_factory=list)


class FaceSelectionService:
    """Builds and queries face candidates used by the interactive viewer."""
    def __init__(self, model, working_plane_normal=(0.0, 0.0, 1.0)):
        self.model = model
        self.working_plane_normal = np.array(working_plane_normal, dtype=float)
        norm = np.linalg.norm(self.working_plane_normal)
        if norm == 0:
            self.working_plane_normal = np.array([0.0, 0.0, 1.0], dtype=float)
        else:
            self.working_plane_normal /= norm
        self._edge_face_map = TopTools_IndexedDataMapOfShapeListOfShape()
        self._candidates: List[FaceCandidate] = []

    def build_candidates(self) -> List[FaceCandidate]:
        """Scan model BRep faces and generate selection candidates.

        The method also computes edge-based adjacency and populates compatible
        feature menus for each candidate.
        """
        if not self.model or not getattr(self.model, "brep", None):
            return []

        brep = self.model.brep
        TopExp.MapShapesAndAncestors_s(brep, TopAbs_EDGE, TopAbs_FACE, self._edge_face_map)

        candidates: List[FaceCandidate] = []
        edge_to_face_ids: Dict[int, set[int]] = {}

        exp = TopExp_Explorer(brep, TopAbs_FACE)
        face_id = 0
        while exp.More():
            face = TopoDS.Face_s(exp.Current())
            center = self._face_center(face)
            surface_type = self._surface_type_name(face)
            accessible = self._face_is_accessible(face)
            candidate = FaceCandidate(
                face_id=face_id,
                face=face,
                center=center,
                surface_type=surface_type,
                accessible_from_top=accessible,
                compatible_features=self._compatible_features(surface_type, accessible),
            )
            candidates.append(candidate)

            exp_e = TopExp_Explorer(face, TopAbs_EDGE)
            while exp_e.More():
                edge_idx = self._edge_face_map.FindIndex(exp_e.Current())
                if edge_idx > 0:
                    edge_to_face_ids.setdefault(edge_idx, set()).add(face_id)
                exp_e.Next()

            face_id += 1
            exp.Next()

        for face_ids in edge_to_face_ids.values():
            if len(face_ids) < 2:
                continue
            for fid in face_ids:
                neighbors = set(candidates[fid].neighbors)
                neighbors.update(other for other in face_ids if other != fid)
                candidates[fid].neighbors = sorted(neighbors)

        self._candidates = candidates
        logger.info(
            "FaceSelectionService: %d facce indicizzate, %d accessibili dall'alto",
            len(candidates),
            sum(1 for c in candidates if c.accessible_from_top),
        )
        return candidates

    @property
    def candidates(self) -> List[FaceCandidate]:
        return self._candidates

    def get_visible_candidates(self, accessible_only: bool = True) -> List[FaceCandidate]:
        """Return candidates currently visible under the active accessibility filter."""
        if not accessible_only:
            return list(self._candidates)
        return [c for c in self._candidates if c.accessible_from_top]

    def find_candidate_by_shape(self, shape, accessible_only: bool = False) -> Optional[FaceCandidate]:
        """Resolve a detected OCC shape to a candidate entry."""
        if shape is None:
            return None
        for candidate in self.get_visible_candidates(accessible_only=accessible_only):
            try:
                if candidate.face.IsSame(shape):
                    return candidate
            except Exception:
                continue
        return None

    def get_candidate(self, face_id: Optional[int]) -> Optional[FaceCandidate]:
        """Return candidate by id, or None for invalid ids."""
        if face_id is None or face_id < 0 or face_id >= len(self._candidates):
            return None
        return self._candidates[face_id]

    def cycle_neighbor(self, current_face_id: Optional[int], step: int = 1, accessible_only: bool = True) -> Optional[FaceCandidate]:
        """Cycle selection using topological neighbors when possible.

        If no valid neighbor remains after filtering, fallback to all visible
        candidates to guarantee deterministic navigation.
        """
        visible = self.get_visible_candidates(accessible_only=accessible_only)
        if not visible:
            return None
        if current_face_id is None:
            return visible[0 if step >= 0 else -1]

        current = self.get_candidate(current_face_id)
        if current is None:
            return visible[0 if step >= 0 else -1]

        candidate_ids = current.neighbors or [c.face_id for c in visible]
        filtered_ids = [fid for fid in candidate_ids if self._candidate_visible(fid, accessible_only)]
        if not filtered_ids:
            filtered_ids = [c.face_id for c in visible]

        if current_face_id in filtered_ids:
            idx = filtered_ids.index(current_face_id)
            next_idx = (idx + step) % len(filtered_ids)
        else:
            next_idx = 0 if step >= 0 else -1
        return self.get_candidate(filtered_ids[next_idx])

    def cycle_visible(self, current_face_id: Optional[int], step: int = 1, accessible_only: bool = True) -> Optional[FaceCandidate]:
        """Cycle across all visible candidates, ignoring adjacency."""
        visible = self.get_visible_candidates(accessible_only=accessible_only)
        if not visible:
            return None

        visible_ids = [c.face_id for c in visible]
        if current_face_id is None or current_face_id not in visible_ids:
            return self.get_candidate(visible_ids[0 if step >= 0 else -1])

        idx = visible_ids.index(current_face_id)
        next_idx = (idx + step) % len(visible_ids)
        return self.get_candidate(visible_ids[next_idx])

    def _candidate_visible(self, face_id: int, accessible_only: bool) -> bool:
        candidate = self.get_candidate(face_id)
        return candidate is not None and (candidate.accessible_from_top or not accessible_only)

    def _surface_type_name(self, face) -> str:
        surf = BRepAdaptor_Surface(face, True)
        stype = surf.GetType()
        if stype == GeomAbs_Plane:
            return "plane"
        if stype == GeomAbs_Cylinder:
            return "cylinder"
        if stype == GeomAbs_Cone:
            return "cone"
        if stype == GeomAbs_Sphere:
            return "sphere"
        if stype == GeomAbs_Torus:
            return "torus"
        return "surface"

    def _compatible_features(self, surface_type: str, accessible: bool) -> List[str]:
        """Map simplified surface classes to manually assignable feature types."""
        if surface_type == "plane":
            if accessible:
                return ["pocket", "step", "opening", "chamfer"]
            return ["chamfer"]
        if surface_type == "cylinder":
            return ["fillet", "hole", "hole_through", "hole_blind"]
        if surface_type == "cone":
            return ["countersunk_hole", "chamfer", "hole_through", "hole_blind"]
        if surface_type in {"sphere", "torus"}:
            return ["fillet"]
        return []

    def can_assign_manual_hole(self, face_ids: List[int], accessible_only: bool = True) -> bool:
        """Validate whether selected faces can represent one manual hole.

        Requirements are intentionally conservative:
        - all faces must be cylindrical/conical walls,
        - at least one cylinder is required,
        - axis must be aligned with tool axis,
        - projected XY centers and radii must be coherent.
        """
        if len(face_ids) < 1:
            return False

        candidates = []
        for fid in face_ids:
            cand = self.get_candidate(fid)
            if cand is None:
                return False
            if accessible_only and not cand.accessible_from_top:
                return False
            if cand.surface_type not in {"cylinder", "cone"}:
                return False
            candidates.append(cand)

        # At least one cylindrical wall is required for standard hole semantics.
        if not any(c.surface_type == "cylinder" for c in candidates):
            return False

        axes = []
        centers_xy = []
        radii = []
        for cand in candidates:
            axis, radius = self._axis_and_radius(cand.face, cand.surface_type)
            if axis is None:
                return False
            # Hole axis must be approximately parallel to tool axis.
            if abs(np.dot(axis, self.working_plane_normal)) < 0.95:
                return False
            axes.append(axis)
            centers_xy.append(np.array(cand.center[:2], dtype=float))
            if radius is not None:
                radii.append(radius)

        # XY-projected centers must belong to the same physical hole.
        ref_center = centers_xy[0]
        for cxy in centers_xy[1:]:
            if np.linalg.norm(cxy - ref_center) > 1.0:
                return False

        # Cylindrical radii must be mutually consistent.
        if radii:
            r0 = radii[0]
            for r in radii[1:]:
                if abs(r - r0) > 0.8:
                    return False

        return True

    def _axis_and_radius(self, face, surface_type: str):
        """Extract normalized axis and optional radius for cylinder/cone faces."""
        try:
            surf = BRepAdaptor_Surface(face, True)
            if surface_type == "cylinder":
                cyl = surf.Cylinder()
                axis = np.array([
                    cyl.Axis().Direction().X(),
                    cyl.Axis().Direction().Y(),
                    cyl.Axis().Direction().Z(),
                ], dtype=float)
                return axis / np.linalg.norm(axis), float(cyl.Radius())
            if surface_type == "cone":
                cone = surf.Cone()
                axis = np.array([
                    cone.Axis().Direction().X(),
                    cone.Axis().Direction().Y(),
                    cone.Axis().Direction().Z(),
                ], dtype=float)
                return axis / np.linalg.norm(axis), None
        except Exception:
            return None, None
        return None, None

    def _face_center(self, face) -> Tuple[float, float, float]:
        """Approximate face center using midpoint of the parametric domain."""
        try:
            surf = BRepAdaptor_Surface(face, True)
            u = (surf.FirstUParameter() + surf.LastUParameter()) / 2
            v = (surf.FirstVParameter() + surf.LastVParameter()) / 2
            pnt = surf.Value(u, v)
            return (pnt.X(), pnt.Y(), pnt.Z())
        except Exception:
            return (0.0, 0.0, 0.0)

    def _face_is_accessible(self, face) -> bool:
        """Heuristic top-accessibility test for 2.5D-style editing.

        Planar faces are validated using normal orientation plus ray obstruction.
        Vertical cylindrical/conical walls aligned with tool axis are accepted to
        support manual hole and fillet wall selection.
        """
        try:
            surf = BRepAdaptor_Surface(face, True)
            stype = surf.GetType()
            u = (surf.FirstUParameter() + surf.LastUParameter()) / 2
            v = (surf.FirstVParameter() + surf.LastVParameter()) / 2
            pnt = surf.Value(u, v)

            geom_surf = BRep_Tool.Surface_s(face)
            props = GeomLProp_SLProps(geom_surf, u, v, 1, 1e-6)
            if not props.IsNormalDefined():
                return False

            occ_n = props.Normal()
            normal = np.array([occ_n.X(), occ_n.Y(), occ_n.Z()], dtype=float)
            if face.Orientation() == TopAbs_REVERSED:
                normal = -normal

            n = self.working_plane_normal
            if stype == GeomAbs_Plane:
                if np.dot(normal, n) < 0.05:
                    return False
            elif stype in {GeomAbs_Cylinder, GeomAbs_Cone}:
                axis, _ = self._axis_and_radius(face, "cylinder" if stype == GeomAbs_Cylinder else "cone")
                if axis is None or abs(np.dot(axis, n)) < 0.95:
                    return False
                # Vertical hole/fillet walls are considered selectable from top view.
                return True
            else:
                if np.dot(normal, n) < 0.05:
                    return False

            eps = 0.05
            start = gp_Pnt(pnt.X() + n[0] * eps, pnt.Y() + n[1] * eps, pnt.Z() + n[2] * eps)
            line = gp_Lin(start, gp_Dir(n[0], n[1], n[2]))
            inter = BRepIntCurveSurface_Inter()
            inter.Init(self.model.brep, line, 1e-4)
            while inter.More():
                if inter.W() > eps * 0.5:
                    return False
                inter.Next()
            return True
        except Exception:
            return False
