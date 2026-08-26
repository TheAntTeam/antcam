from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Circle
from OCP.gp import gp_Pnt, gp_Dir, gp_Vec, gp_Lin
from OCP.Bnd import Bnd_Box
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_REVERSED, TopAbs_FORWARD
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape
from OCP.IntCurveSurface import IntCurveSurface_IntersectionPoint

from antcam.features import (
    ChamferFeature,
    CountersunkHoleFeature,
    Feature,
    FilletFeature,
    HoleFeature,
    HoleGroup,
    OpeningFeature,
    PerimeterFeature,
    PocketFeature,
    SlotFeature,
    StepFeature,
)
from antcam.logger import setup_logger

log = setup_logger()


@dataclass
class RecognitionTolerances:
    axis_parallel: float = 0.01
    axis_coaxial: float = 0.01
    planar_distance: float = 0.05
    edge_open_ratio: float = 0.8
    hole_group_angle: float = 0.05
    hole_group_depth: float = 1.0
    slot_width_ratio: float = 0.15
    chamfer_angle_min: float = 15.0
    chamfer_angle_max: float = 75.0


_WORKING_AXIS = gp_Dir(0, 0, 1)


def _is_tool_axis(axis: gp_Dir, tol: float = 0.01) -> bool:
    return abs(axis.Dot(_WORKING_AXIS)) > 1.0 - tol


def _is_vertical(axis: gp_Dir, tol: float = 0.01) -> bool:
    return _is_tool_axis(axis, tol)


def _is_horizontal(normal: gp_Dir, tol: float = 0.01) -> bool:
    return abs(normal.Dot(_WORKING_AXIS)) < tol


def _face_surface_type(face: TopoDS_Face) -> int:
    surf = BRepAdaptor_Surface(face)
    return surf.GetType()


def _face_plane_normal(face: TopoDS_Face) -> Optional[gp_Dir]:
    if _face_surface_type(face) != GeomAbs_Plane:
        return None
    surf = BRepAdaptor_Surface(face)
    return surf.Plane().Axis().Direction()


def _face_center(face: TopoDS_Face) -> gp_Pnt:
    surf = BRepAdaptor_Surface(face)
    u1 = surf.FirstUParameter()
    u2 = surf.LastUParameter()
    v1 = surf.FirstVParameter()
    v2 = surf.LastVParameter()
    return surf.Value((u1 + u2) / 2, (v1 + v2) / 2)


def _face_area_est(face: TopoDS_Face) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return props.Mass()


def _edge_count(face: TopoDS_Face) -> int:
    exp = TopExp_Explorer(face, TopAbs_EDGE)
    count = 0
    while exp.More():
        count += 1
        exp.Next()
    return count


_LARGE_Z = 1e6


def _ray_intersects(start: gp_Pnt, direction: gp_Dir, shape: TopoDS_Shape) -> bool:
    inters = BRepIntCurveSurface_Inter()
    line = gp_Lin(start, direction)
    inters.Init(shape, line, 0.001)
    while inters.More():
        pt = inters.Pnt()
        z_offset = pt.Z() - start.Z()
        if abs(z_offset) > 0.01:
            return True
        inters.Next()
    return False


def _classify_face(face: TopoDS_Face, tol: RecognitionTolerances) -> dict:
    st = _face_surface_type(face)
    result = {"type": "unknown", "normal": None, "area": _face_area_est(face), "n_edges": _edge_count(face)}

    if st == GeomAbs_Plane:
        normal = _face_plane_normal(face)
        if normal is None:
            return result
        result["normal"] = (normal.X(), normal.Y(), normal.Z())
        if _is_vertical(normal):
            result["type"] = "horizontal_plane"
        elif _is_horizontal(normal):
            result["type"] = "vertical_plane"
        else:
            result["type"] = "inclined_plane"
            angle = 90.0 - math.degrees(math.acos(abs(normal.Z())))
            result["inclination"] = angle

    elif st == GeomAbs_Cylinder:
        surf = BRepAdaptor_Surface(face)
        cyl = surf.Cylinder()
        axis = cyl.Axis().Direction()
        radius = cyl.Radius()
        result["type"] = "cylinder"
        result["axis"] = (axis.X(), axis.Y(), axis.Z())
        result["radius"] = radius
        result["is_vertical_axis"] = _is_vertical(axis)
        result["is_horizontal_axis"] = _is_horizontal(axis)
        result["orientation"] = "reversed" if face.Orientation() == TopAbs_REVERSED else "forward"

    elif st == GeomAbs_Cone:
        surf = BRepAdaptor_Surface(face)
        cone = surf.Cone()
        axis = cone.Axis().Direction()
        sa = cone.SemiAngle()
        semi_angle = math.degrees(sa) if sa is not None else 0
        result["type"] = "cone"
        result["axis"] = (axis.X(), axis.Y(), axis.Z())
        result["semi_angle"] = semi_angle

    return result


def _circular_edges(face: TopoDS_Face, axis: gp_Dir) -> list:
    edges = []
    exp = TopExp_Explorer(face, TopAbs_EDGE)
    while exp.More():
        edge = TopoDS.Edge_s(exp.Current())
        adapt = BRepAdaptor_Curve(edge)
        if adapt.GetType() == GeomAbs_Circle:
            circle = adapt.Circle()
            if abs(circle.Axis().Direction().Dot(axis)) > 0.99:
                edges.append((edge, adapt, circle))
        exp.Next()
    return edges


# ── Detectors ─────────────────────────────────────────────────────────


def _find_outer_perimeter(shape: TopoDS_Shape, faces: list[TopoDS_Face], classified: dict[int, dict]) -> set[int]:
    bbox = Bnd_Box()
    BRepBndLib.Add_s(shape, bbox)
    gxmin, gymin, gzmin, gxmax, gymax, gzmax = bbox.Get()
    tol = 0.1

    perimeter: set[int] = set()
    for idx, face in enumerate(faces):
        info = classified.get(idx, {})
        if info.get("type") not in ("vertical_plane", "cylinder"):
            continue

        fb = Bnd_Box()
        BRepBndLib.Add_s(face, fb)
        fxmin, fymin, fzmin, fxmax, fymax, fzmax = fb.Get()

        if (abs(fxmin - gxmin) < tol or abs(fxmax - gxmax) < tol or
            abs(fymin - gymin) < tol or abs(fymax - gymax) < tol):
            perimeter.add(idx)

    return perimeter


class HoleDetector:
    def __init__(self, tol: RecognitionTolerances):
        self.tol = tol

    def detect(self, faces: list[TopoDS_Face], classified: dict[int, dict], shape: Optional[TopoDS_Shape] = None) -> list[HoleFeature]:
        holes: list[HoleFeature] = []
        for idx, face in enumerate(faces):
            info = classified.get(idx, {})
            if info.get("type") != "cylinder":
                continue
            if info.get("orientation") != "reversed":
                continue

            surf = BRepAdaptor_Surface(face)
            cyl = surf.Cylinder()
            radius = cyl.Radius()
            axis = cyl.Axis().Direction()
            center_pnt = cyl.Location()

            u1, u2 = surf.FirstUParameter(), surf.LastUParameter()
            p1, p2 = surf.Value(u1, 0), surf.Value(u2, 0)
            depth = p1.Distance(p2)
            if depth < 0.01:
                continue

            center = (center_pnt.X(), center_pnt.Y(), center_pnt.Z() + depth / 2)

            through = self._is_through(face, axis, shape or faces[0])

            holes.append(HoleFeature(
                diameter=2 * radius,
                axis=(axis.X(), axis.Y(), axis.Z()),
                center=center,
                depth=depth,
                through=through,
                geometry=face,
                face_indices={idx},
            ))

        return self._merge_countersinks(holes)

    def _is_through(self, face: TopoDS_Face, axis: gp_Dir, body: TopoDS_Shape) -> bool:
        circles = _circular_edges(face, axis)
        if len(circles) < 2:
            return True
        _, _, c1 = circles[0]
        _, _, c2 = circles[1]
        p1, p2 = c1.Location(), c2.Location()
        mid = gp_Pnt((p1.X() + p2.X()) / 2, (p1.Y() + p2.Y()) / 2, (p1.Z() + p2.Z()) / 2)
        # Shoot ray along +axis from p1, along -axis from p2
        d_plus = axis
        d_minus = gp_Dir(-axis.X(), -axis.Y(), -axis.Z())
        hit_plus = _ray_intersects(gp_Pnt(p1.X(), p1.Y(), p1.Z()), d_plus, body)
        hit_minus = _ray_intersects(gp_Pnt(p2.X(), p2.Y(), p2.Z()), d_minus, body)
        if hit_plus and hit_minus:
            return False
        return True

    def _merge_countersinks(self, holes: list[HoleFeature]) -> list[HoleFeature]:
        return holes


class PocketStepDetector:
    def __init__(self, tol: RecognitionTolerances):
        self.tol = tol

    def detect(self, faces: list[TopoDS_Face], classified: dict[int, dict], shape: Optional[TopoDS_Shape] = None) -> list[Feature]:
        pockets: list[PocketFeature] = []
        steps: list[StepFeature] = []
        openings: list[OpeningFeature] = []

        h_planes = [(idx, info) for idx, info in classified.items() if info.get("type") == "horizontal_plane"]
        top_z = max((_face_center(faces[idx]).Z() for idx, _ in h_planes), default=0)

        for idx, face in enumerate(faces):
            info = classified.get(idx, {})
            if info.get("type") != "horizontal_plane":
                continue

            center = _face_center(face)
            if center.Z() < top_z - 0.01 and info["n_edges"] >= 3:
                wall_indices = self._find_adjacent_walls(idx, faces, classified)
                if len(wall_indices) >= 3:
                    depth = top_z - center.Z()
                    all_idx = wall_indices | {idx}
                    pockets.append(PocketFeature(face=face, depth=depth, area=info["area"], face_indices=all_idx))
                else:
                    steps.append(StepFeature(face=face, depth=center.Z(), face_indices={idx}))

        return pockets + steps + openings

    def _find_adjacent_walls(self, face_idx: int, all_faces: list, classified: dict) -> set[int]:
        walls: set[int] = set()
        face = all_faces[face_idx]
        exp = TopExp_Explorer(face, TopAbs_EDGE)
        while exp.More():
            edge = TopoDS.Edge_s(exp.Current())
            for fi, other in enumerate(all_faces):
                if fi == face_idx or fi in walls:
                    continue
                oinfo = classified.get(fi, {})
                if oinfo.get("type") not in ("vertical_plane", "cylinder"):
                    continue
                oexp = TopExp_Explorer(other, TopAbs_EDGE)
                while oexp.More():
                    if oexp.Current().IsSame(edge):
                        walls.add(fi)
                        break
                    oexp.Next()
            exp.Next()
        return walls


class SlotDetector:
    def __init__(self, tol: RecognitionTolerances):
        self.tol = tol

    def detect(self, faces: list[TopoDS_Face], classified: dict[int, dict], shape: Optional[TopoDS_Shape] = None) -> list[SlotFeature]:
        slots: list[SlotFeature] = []
        cyl_faces = [(idx, info) for idx, info in classified.items() if info.get("type") == "cylinder"]

        for i, (i1, c1) in enumerate(cyl_faces):
            for i2, c2 in cyl_faces[i + 1:]:
                a1 = np.array(c1["axis"])
                a2 = np.array(c2["axis"])
                if abs(np.dot(a1, a2)) < 1 - self.tol.axis_parallel:
                    continue
                if abs(c1["radius"] - c2["radius"]) / max(c1["radius"], 0.01) > self.tol.slot_width_ratio:
                    continue
                if c1.get("orientation") == c2.get("orientation"):
                    continue

                width = (c1["radius"] + c2["radius"]) * 2

                f1, f2 = faces[i1], faces[i2]
                c1_surf = BRepAdaptor_Surface(f1)
                c2_surf = BRepAdaptor_Surface(f2)
                u1a, u1b = c1_surf.FirstUParameter(), c1_surf.LastUParameter()
                u2a, u2b = c2_surf.FirstUParameter(), c2_surf.LastUParameter()
                p1a = c1_surf.Value(u1a, 0)
                p1b = c1_surf.Value(u1b, 0)
                p2a = c2_surf.Value(u2a, 0)
                p2b = c2_surf.Value(u2b, 0)
                depth = max(p1a.Distance(p1b), p2a.Distance(p2b))
                length = p1a.Distance(p2a)

                slots.append(SlotFeature(
                    width=width,
                    length=length,
                    depth=depth,
                    axis=c1["axis"],
                    geometry=faces[i1],
                    face_indices={i1, i2},
                ))
                break

        return slots


class FilletChamferDetector:
    def __init__(self, tol: RecognitionTolerances):
        self.tol = tol

    def detect(self, faces: list[TopoDS_Face], classified: dict[int, dict], shape: Optional[TopoDS_Shape] = None) -> tuple[list[FilletFeature], list[ChamferFeature]]:
        fillets: list[FilletFeature] = []
        chamfers: list[ChamferFeature] = []

        for idx, face in enumerate(faces):
            info = classified.get(idx, {})
            if info.get("type") == "cylinder" and info.get("is_horizontal_axis", False) and info.get("orientation") == "forward":
                fillets.append(FilletFeature(radius=info["radius"], geometry=face, face_indices={idx}))
            elif info.get("type") == "inclined_plane":
                angle = info.get("inclination", 0)
                chamfers.append(ChamferFeature(width=0, angle=angle, geometry=face, face_indices={idx}))

        return fillets, chamfers


# ── Pipeline ──────────────────────────────────────────────────────────


class FeatureExtractor:
    def __init__(self, tol: Optional[RecognitionTolerances] = None):
        self.tol = tol or RecognitionTolerances()
        self.detectors = [
            HoleDetector(self.tol),
            SlotDetector(self.tol),
            PocketStepDetector(self.tol),
            FilletChamferDetector(self.tol),
        ]

    def extract(self, shape) -> list[Feature]:
        faces = list(self._iter_faces(shape))
        classified = {idx: _classify_face(f, self.tol) for idx, f in enumerate(faces)}

        all_features: list[Feature] = []

        for detector in self.detectors:
            result = detector.detect(faces, classified, shape)
            if isinstance(result, tuple):
                for r in result:
                    all_features.extend(r)
            elif isinstance(result, list):
                all_features.extend(result)

        # Outer perimeter
        perimeter_faces = _find_outer_perimeter(shape, faces, classified)
        if perimeter_faces:
            feat = PerimeterFeature(face_indices=perimeter_faces)
            feat.props["face_count"] = len(perimeter_faces)
            all_features.append(feat)

        # Group holes by diameter+depth
        hole_features = [f for f in all_features if isinstance(f, HoleFeature)]
        groups = self._group_holes(hole_features)
        all_features = [f for f in all_features if not isinstance(f, HoleFeature)]
        all_features.extend(groups)

        counts = {f.type: 0 for f in all_features}
        for f in all_features:
            counts[f.type] = counts.get(f.type, 0) + 1
        log.info(f"Features found: {dict(counts)}")

        return all_features

    def _iter_faces(self, shape):
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            yield TopoDS.Face_s(exp.Current())
            exp.Next()

    def _group_holes(self, holes: list[HoleFeature]) -> list[Feature]:
        result: list[Feature] = []
        used = set()
        for i, h1 in enumerate(holes):
            if i in used:
                continue
            group = [h1]
            used.add(i)
            for j, h2 in enumerate(holes):
                if j in used:
                    continue
                if abs(h1.diameter - h2.diameter) > 0.1:
                    continue
                if abs(h1.depth - h2.depth) > self.tol.hole_group_depth:
                    continue
                if h1.through != h2.through:
                    continue
                group.append(h2)
                used.add(j)
            result.append(HoleGroup(
                diameter=h1.diameter,
                depth=h1.depth,
                through=h1.through,
                holes=group,
            ))
        return result
