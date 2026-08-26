"""Unified slicing module with BRep vs mesh auto-validation.

Strategy per ogni Z level:
  1. Mesh slicing via trimesh (sempre, veloce, robusto)
  2. BRep sectioning via BRepAlgoAPI_Section (se BRep disponibile)
  3. Cross-validazione: confronta mesh vs brep
  4. Usa BRep se confidence >= 95%, altrimenti mesh approssimato
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
from typing import Any, Optional

import numpy as np

logger = logging.getLogger("antcam")


class SliceResult:
    """Result of slicing at one Z level."""

    def __init__(
        self,
        polylines: list[list[tuple[float, float, float]]],
        source: str = "mesh",
        confidence: float = 1.0,
        validation: str = "none",
        z: float = 0.0,
    ):
        self.polylines = polylines
        self.source = source
        self.confidence = confidence
        self.validation = validation
        self.z = z

    def __repr__(self):
        return (
            f"SliceResult(z={self.z:.3f}, source={self.source}, "
            f"confidence={self.confidence:.3f}, loops={len(self.polylines)})"
        )


def slice_shape(
    shape: Any,
    z_levels: list[float],
    mesh: Optional[Any] = None,
    working_plane_normal: tuple[float, float, float] = (0.0, 0.0, 1.0),
    deflection: float = 0.05,
    validation_tol: float = 0.05,
) -> dict[float, SliceResult]:
    """Slice a shape at given Z levels with BRep vs mesh auto-validation.

    Args:
        shape: OCC BRep shape (None for mesh-only)
        z_levels: Z projection values
        mesh: Pre-computed trimesh (None → tessellate from BRep)
        working_plane_normal: Working plane normal
        deflection: Mesh tessellation quality (lower = finer)
        validation_tol: Tolerance for BRep vs mesh validation

    Returns:
        dict mapping Z → SliceResult
    """
    if mesh is None and shape is not None:
        mesh = _tessellate_brep(shape, deflection)
        if mesh is not None:
            logger.info(
                "Tessellated mesh: %d faces, %.2f%% watertight",
                len(mesh.faces),
                mesh.watertight * 100.0 if hasattr(mesh, "watertight") else 0.0,
            )

    results: dict[float, SliceResult] = {}
    mesh_cache: Optional[Any] = mesh
    z_sorted = sorted(z_levels)

    for z in z_sorted:
        mesh_polylines: list[list[tuple[float, float, float]]] = []
        if mesh_cache is not None:
            try:
                mesh_polylines = _slice_mesh_at_z(mesh_cache, z, working_plane_normal)
            except Exception as exc:
                logger.debug("Mesh slice failed at Z=%.3f: %s", z, exc)

        brep_polylines: list[list[tuple[float, float, float]]] = []
        if shape is not None:
            try:
                brep_polylines = _brep_section_exact(shape, z, working_plane_normal)
            except Exception as exc:
                logger.debug("BRep section failed at Z=%.3f: %s", z, exc)

        if brep_polylines and mesh_polylines:
            confidence, reason = _validate_brep_vs_mesh(brep_polylines, mesh_polylines)
            if confidence >= 0.80:
                results[z] = SliceResult(
                    polylines=brep_polylines,
                    source="brep_exact",
                    confidence=confidence,
                    validation=reason,
                    z=z,
                )
            else:
                logger.info(
                    "Slice Z=%.3f: BRep confidence %.3f (%s) — using mesh",
                    z, confidence, reason,
                )
                results[z] = SliceResult(
                    polylines=mesh_polylines,
                    source="mesh_approximate",
                    confidence=confidence,
                    validation=reason,
                    z=z,
                )
        elif brep_polylines:
            results[z] = SliceResult(
                polylines=brep_polylines,
                source="brep_exact",
                confidence=0.8,
                validation="mesh_empty",
                z=z,
            )
        elif mesh_polylines:
            results[z] = SliceResult(
                polylines=mesh_polylines,
                source="mesh_approximate",
                confidence=0.85,
                validation="brep_failed" if shape is not None else "brep_unavailable",
                z=z,
            )
            if shape is not None:
                logger.warning("Slice Z=%.3f: BRep failed, mesh fallback", z)
        else:
            results[z] = SliceResult(
                polylines=[],
                source="none",
                confidence=0.0,
                validation="no_result",
                z=z,
            )

    return results


# ---------------------------------------------------------------------------
# BRep → trimesh tessellation
# ---------------------------------------------------------------------------


def _tessellate_brep(shape: Any, deflection: float = 0.1) -> Optional[Any]:
    """Tessellate an OCC BRep shape into a trimesh via temporary STL."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.StlAPI import StlAPI_Writer

    try:
        mesh_tool = BRepMesh_IncrementalMesh(shape, deflection)
        mesh_tool.Perform()
        if not mesh_tool.IsDone():
            logger.warning("BRepMesh_IncrementalMesh not done")
            return None
    except Exception as exc:
        logger.warning("BRepMesh failed: %s", exc)
        return None

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as f:
            tmp_path = f.name

        writer = StlAPI_Writer()
        writer.ASCIIMode = False
        writer.Write(shape, tmp_path)

        if not os.path.getsize(tmp_path):
            logger.warning("STL export produced empty file")
            return None

        import trimesh

        mesh = trimesh.load_mesh(tmp_path)
        if mesh.is_empty:
            return None
        mesh.remove_unreferenced_vertices()
        return mesh
    except Exception as exc:
        logger.warning("Tessellation failed: %s", exc)
        return None
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Mesh slicing (via trimesh.section)
# ---------------------------------------------------------------------------


def _slice_mesh_at_z(
    mesh: Any,
    z: float,
    normal: tuple[float, float, float],
) -> list[list[tuple[float, float, float]]]:
    """Slice a trimesh at a Z level using trimesh.section()."""
    nx, ny, nz = normal
    origin = (nx * z, ny * z, nz * z)

    try:
        path = mesh.section(plane_origin=origin, plane_normal=normal)
    except Exception:
        try:
            path = mesh.section(plane_origin=origin, plane_normal=normal, cached=True)
        except Exception:
            return []

    if path is None:
        return []

    return _path3d_to_polylines(path, z)


def _path3d_to_polylines(
    path: Any,
    z: float,
) -> list[list[tuple[float, float, float]]]:
    """Extract closed 3D polylines from a trimesh Path3D and clamp Z."""
    from trimesh.path.entities import Line

    polylines: list[list[tuple[float, float, float]]] = []

    for entity in path.entities:
        if not isinstance(entity, Line):
            continue
        try:
            pts = entity.discrete(path.vertices)
        except Exception:
            continue
        if pts.shape[0] < 3:
            continue

        poly: list[tuple[float, float, float]] = []
        for i in range(pts.shape[0]):
            x = float(pts[i, 0])
            y = float(pts[i, 1])
            poly.append((x, y, z))

        # Remove duplicate closure vertex
        if len(poly) > 1:
            dx = poly[-1][0] - poly[0][0]
            dy = poly[-1][1] - poly[0][1]
            if dx * dx + dy * dy < 1e-12:
                poly = poly[:-1]

        if len(poly) >= 3:
            polylines.append(poly)

    return polylines


# ---------------------------------------------------------------------------
# BRep exact section (via BRepAlgoAPI_Section)
# ---------------------------------------------------------------------------


def _brep_section_get_wires(
    shape: Any,
    projection: float,
    normal: Any,
) -> list[Any]:
    """Section an OCC BRep at a projection → return list of OCC Wires."""
    from OCP.gp import gp_Pnt, gp_Dir, gp_Ax3, gp_Pln
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopAbs import TopAbs_WIRE, TopAbs_EDGE
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_HSequenceOfShape
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.BRep import BRep_Tool

    nx = float(normal[0])
    ny = float(normal[1])
    nz = float(normal[2])
    pj = float(projection)

    origin = gp_Pnt(nx * pj, ny * pj, nz * pj)
    gp_normal = gp_Dir(nx, ny, nz)

    ref = gp_Dir(1, 0, 0)
    if abs(nx) > 0.9:
        ref = gp_Dir(0, 1, 0)
    u_dir = ref.Crossed(gp_normal)

    plane = gp_Pln(gp_Ax3(origin, gp_normal, u_dir))

    section_algo = BRepAlgoAPI_Section(shape, plane)
    section_algo.Build()
    if not section_algo.IsDone():
        return []

    section_shape = section_algo.Shape()

    exp_e = TopExp_Explorer(section_shape, TopAbs_EDGE)
    edges_seq = TopTools_HSequenceOfShape()
    while exp_e.More():
        edge = TopoDS.Edge_s(exp_e.Current())
        # Salta edge degenerati (collassati a punto) — impediscono
        # a ConnectEdgesToWires di formare wire corretti.
        if not BRep_Tool.Degenerated_s(edge):
            edges_seq.Append(edge)
        exp_e.Next()

    if edges_seq.Length() == 0:
        return []

    wires_seq = TopTools_HSequenceOfShape()
    ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(edges_seq, 1e-6, True, wires_seq)

    if wires_seq.Length() == 0:
        ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(edges_seq, 1e-4, True, wires_seq)
    if wires_seq.Length() == 0:
        ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(edges_seq, 1e-2, True, wires_seq)
    if wires_seq.Length() == 0:
        return []

    wires: list[Any] = []
    for i in range(1, wires_seq.Length() + 1):
        wire = TopoDS.Wire_s(wires_seq.Value(i))
        exp_w = TopExp_Explorer(wire, TopAbs_EDGE)
        ne = 0
        while exp_w.More():
            ne += 1
            exp_w.Next()
        if ne >= 2:
            wires.append(wire)
        elif ne == 1:
            edge = TopoDS.Edge_s(TopExp_Explorer(wire, TopAbs_EDGE).Current())
            v1 = TopExp.FirstVertex_s(edge)
            v2 = TopExp.LastVertex_s(edge)
            if v1.IsSame(v2):
                wires.append(wire)

    return wires


def _brep_wire_to_polyline(
    wire: Any,
    samples_per_edge: int = 8,
    max_deflection: float = 0.05,
) -> list[tuple[float, float, float]]:
    """Sample an OCC wire into a polyline with proper curve subdivision."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopoDS import TopoDS
    import numpy as np

    pts: list[tuple[float, float, float]] = []
    exp_edges = BRepTools_WireExplorer(wire)

    while exp_edges.More():
        edge = TopoDS.Edge_s(exp_edges.Current())
        curve = BRepAdaptor_Curve(edge)
        t_first = curve.FirstParameter()
        t_last = curve.LastParameter()

        sampled = _sample_curve_uniform(curve, t_first, t_last, samples_per_edge, max_deflection)

        if edge.Orientation() == TopAbs_REVERSED:
            sampled.reverse()

        for s in sampled:
            if not pts or np.linalg.norm(np.array(pts[-1]) - np.array(s)) > 1e-7:
                pts.append(s)

        exp_edges.Next()

    return pts


def _sample_curve_uniform(
    curve: Any,
    t_first: float,
    t_last: float,
    samples_per_edge: int = 8,
    max_deflection: float = 0.05,
    max_depth: int = 10,
) -> list[tuple[float, float, float]]:
    """Sample a curve with uniform+adaptive subdivision."""
    from OCP.GeomAbs import GeomAbs_Line
    import numpy as np

    tf = float(t_first)
    tl = float(t_last)
    if abs(tl - tf) <= 1e-12:
        p = curve.Value(tf)
        return [(float(p.X()), float(p.Y()), float(p.Z()))]

    n = max(2, int(samples_per_edge))
    params = [tf + (tl - tf) * i / (n - 1) for i in range(n)]

    pts: list[tuple[float, float, float]] = []
    for i in range(len(params) - 1):
        seg_pts = _sample_curve_segment(curve, params[i], params[i + 1], max_deflection, max_depth)
        if not seg_pts:
            continue
        if pts and np.linalg.norm(np.array(pts[-1]) - np.array(seg_pts[0])) <= 1e-7:
            pts.extend(seg_pts[1:])
        else:
            pts.extend(seg_pts)

    return pts


def _sample_curve_segment(
    curve: Any,
    t_start: float,
    t_end: float,
    max_deflection: float = 0.05,
    remaining_depth: int = 10,
) -> list[tuple[float, float, float]]:
    """Adaptively subdivide a curve segment until flatness is below threshold."""
    from OCP.GeomAbs import GeomAbs_Line
    import numpy as np

    if remaining_depth <= 0:
        p1 = curve.Value(float(t_start))
        p2 = curve.Value(float(t_end))
        return [(float(p1.X()), float(p1.Y()), float(p1.Z())),
                (float(p2.X()), float(p2.Y()), float(p2.Z()))]

    t_mid = (float(t_start) + float(t_end)) * 0.5

    p1 = curve.Value(float(t_start))
    pm = curve.Value(t_mid)
    p2 = curve.Value(float(t_end))

    # Midpoint deflection check
    mid_pt = np.array([float(pm.X()), float(pm.Y()), float(pm.Z())])
    chord_pt = (np.array([float(p1.X()), float(p1.Y()), float(p1.Z())]) +
                np.array([float(p2.X()), float(p2.Y()), float(p2.Z())])) * 0.5
    deviation = float(np.linalg.norm(mid_pt - chord_pt))

    if deviation <= float(max_deflection) or remaining_depth <= 1:
        return [(float(p1.X()), float(p1.Y()), float(p1.Z())),
                (float(p2.X()), float(p2.Y()), float(p2.Z()))]

    left = _sample_curve_segment(curve, t_start, t_mid, max_deflection, remaining_depth - 1)
    right = _sample_curve_segment(curve, t_mid, t_end, max_deflection, remaining_depth - 1)
    return left[:-1] + right


def _brep_section_exact(
    shape: Any,
    projection: float,
    normal: Any,
) -> list[list[tuple[float, float, float]]]:
    """Section an OCC BRep at a projection → quality-sampled polylines."""
    wires = _brep_section_get_wires(shape, projection, normal)
    if not wires:
        return []

    polylines: list[list[tuple[float, float, float]]] = []
    for wire in wires:
        pts = _brep_wire_to_polyline(wire)
        if len(pts) >= 3:
            polylines.append(pts)

    return polylines


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _loop_signed_area_xy(loop: list[tuple[float, float, float]]) -> float:
    """Signed area projected onto XY. Positive = CCW (outer)."""
    n = len(loop)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += loop[i][0] * loop[j][1] - loop[j][0] * loop[i][1]
    return area * 0.5


def _loop_centroid_xy(loop: list[tuple[float, float, float]]) -> tuple[float, float]:
    n = len(loop)
    if n == 0:
        return (0.0, 0.0)
    cx = sum(p[0] for p in loop) / n
    cy = sum(p[1] for p in loop) / n
    return (cx, cy)


def _hausdorff_distance_2d(
    loop_a: list[tuple[float, float, float]],
    loop_b: list[tuple[float, float, float]],
) -> float:
    """Bidirectional 2D Hausdorff distance (ignores Z)."""
    if not loop_a or not loop_b:
        return float("inf")

    def _one_sided(a, b):
        max_d = 0.0
        for pa in a:
            min_d = float("inf")
            for pb in b:
                dx = pa[0] - pb[0]
                dy = pa[1] - pb[1]
                d = math.sqrt(dx * dx + dy * dy)
                if d < min_d:
                    min_d = d
            if min_d > max_d:
                max_d = min_d
        return max_d

    return max(_one_sided(loop_a, loop_b), _one_sided(loop_b, loop_a))


def _validate_brep_vs_mesh(
    brep_loops: list[list[tuple[float, float, float]]],
    mesh_loops: list[list[tuple[float, float, float]]],
) -> tuple[float, str]:
    """Compare BRep vs mesh loops → (confidence 0-1, reason).

    Pairs loops by nearest centroid (not by area sorting) to handle
    identical-area holes correctly. Uses area ratio + centroid distance.
    Hausdorff deliberately excluded — mesh resolution is typically
    coarser than BRep curves, making point-to-point distances
    misleading even when both represent the same shape.
    """
    if not brep_loops:
        return 0.0, "no_brep_loops"
    if not mesh_loops:
        return 0.7, "no_mesh_loops"

    # Loop count mismatch → penalize but don't kill
    if len(brep_loops) != len(mesh_loops):
        return max(0.55, 1.0 - abs(len(brep_loops) - len(mesh_loops)) * 0.1), \
               f"loop_count_brep={len(brep_loops)}_mesh={len(mesh_loops)}"

    # Pair loops by nearest centroid (handles identical-area holes correctly)
    unused = list(range(len(mesh_loops)))
    pairs: list[tuple[list, list]] = []
    for bl in brep_loops:
        cb = _loop_centroid_xy(bl)
        best_d = float("inf")
        best_j = -1
        for j in unused:
            cm = _loop_centroid_xy(mesh_loops[j])
            d = math.sqrt((cb[0] - cm[0])**2 + (cb[1] - cm[1])**2)
            if d < best_d:
                best_d = d
                best_j = j
        if best_j >= 0:
            pairs.append((bl, mesh_loops[best_j]))
            unused.remove(best_j)

    scores: list[float] = []
    for bl, ml in pairs:
        area_b = abs(_loop_signed_area_xy(bl))
        area_m = abs(_loop_signed_area_xy(ml))
        denom = max(area_b, area_m)
        area_ratio = min(area_b, area_m) / denom if denom > 1e-9 else 1.0

        ca = _loop_centroid_xy(bl)
        cb = _loop_centroid_xy(ml)
        cd = math.sqrt((ca[0] - cb[0])**2 + (ca[1] - cb[1])**2)
        c_score = max(0.0, 1.0 - cd * 2.0)

        scores.append(area_ratio * 0.6 + c_score * 0.4)

    overall = sum(scores) / len(scores) if scores else 0.0
    reason = "ok" if overall >= 0.80 else f"low_confidence_{overall:.3f}"
    return overall, reason


# ---------------------------------------------------------------------------
# BRep wire classification + face creation (BBox containment)
# ---------------------------------------------------------------------------


def _sample_test_points(
    pts: list[tuple[float, float, float]],
    n: int,
) -> list[tuple[float, float, float]]:
    """Sample n evenly-spaced points from a polyline for containment testing."""
    if n <= 1 or len(pts) <= n:
        return pts[:max(1, n)]
    indices = [int(i * (len(pts) - 1) / (n - 1)) for i in range(n)]
    return [pts[i] for i in indices]


def _brep_section_classify_wires(
    wires: list[Any],
) -> list[tuple[Any, list[Any]]]:
    """Classify wires into (outer, [holes]) by signed area + point containment.

    Converts each wire to a 2D polyline (XY plane), computes signed area
    to determine orientation (CCW=outer, CW=hole), then uses ray-casting
    point-in-polygon to determine nesting.

    Returns::

        [(outer_wire, [hole_wire, ...]), ...]

    Each tuple is one independent region at the Z level.
    "Holes" includes EVERY inner wire contained by the outer,
    regardless of orientation (CW holes or CCW islands).
    """
    data: list[dict[str, Any]] = []
    for wire in wires:
        try:
            pts = _brep_wire_to_polyline(wire)
        except Exception:
            continue
        if len(pts) < 3:
            continue
        area = _loop_signed_area_xy(pts)
        a_abs = abs(area)
        if a_abs < 1e-9:
            continue
        data.append({
            "wire": wire,
            "pts": pts,
            "area": area,
            "a_abs": a_abs,
        })

    if not data:
        return []

    # Sort by |area| descending (largest → outer candidates)
    data.sort(key=lambda d: d["a_abs"], reverse=True)

    n = len(data)
    parent: list[int] = [-1] * n

    for i in range(n):
        d_i = data[i]
        test_pts_i = _sample_test_points(d_i["pts"], 5)
        best_j = -1
        best_abs = float("inf")
        for j in range(i - 1, -1, -1):
            a_abs_j = data[j]["a_abs"]
            if a_abs_j >= best_abs or a_abs_j <= d_i["a_abs"]:
                continue
            poly_j = data[j]["pts"]
            inside_count = sum(
                1 for tp in test_pts_i
                if _point_in_2d_polygon((tp[0], tp[1]), poly_j)
            )
            if inside_count >= len(test_pts_i) // 2:
                best_j = j
                best_abs = a_abs_j
        parent[i] = best_j

    # Build children map
    children: dict[int, list[int]] = {i: [] for i in range(n)}
    for i in range(n):
        p = parent[i]
        if p != -1:
            children[p].append(i)

    groups: list[tuple[Any, list[Any]]] = []

    for i in range(n):
        if parent[i] != -1:
            continue
        # Collect ALL descendants of root i as holes (not just direct children)
        holes: list[Any] = []
        stack = list(children.get(i, []))
        while stack:
            ci = stack.pop()
            holes.append(data[ci]["wire"])
            stack.extend(children.get(ci, []))
        groups.append((data[i]["wire"], holes))

    return groups


def _point_in_2d_polygon(
    pt: tuple[float, float],
    poly: list[tuple[float, float, float]],
) -> bool:
    """Ray-casting point-in-polygon test. pt=(x,y), poly has (x,y,z)."""
    x, y = pt
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi) + xi
        ):
            inside = not inside
        j = i
    return inside


def _wires_to_face_with_holes(
    outer_wire: Any,
    hole_wires: list[Any],
) -> Any:
    """Build a TopoDS_Face from an outer wire with holes.

    Uses BRepAlgoAPI_Cut for each hole (handles adjacent/touching holes).
    Falls back to BRepBuilderAPI_MakeFace for simple cases.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS

    outer_rev = TopoDS.Wire_s(outer_wire.Reversed())
    face = BRepBuilderAPI_MakeFace(outer_rev, True).Face()

    for hw in hole_wires:
        hole_rev = TopoDS.Wire_s(hw.Reversed())
        hole_face = BRepBuilderAPI_MakeFace(hole_rev, True).Face()
        try:
            cut = BRepAlgoAPI_Cut(face, hole_face)
            cut.Build()
            if cut.IsDone():
                result = cut.Shape()
                exp_f = TopExp_Explorer(result, TopAbs_FACE)
                if exp_f.More():
                    face = TopoDS.Face_s(exp_f.Current())
        except Exception:
            pass

    analyzer = BRepCheck_Analyzer(face)
    if not analyzer.IsValid():
        from OCP.ShapeFix import ShapeFix_Face
        fix = ShapeFix_Face()
        fix.Init(face)
        fix.Perform()
        face = fix.Face()

    return face
