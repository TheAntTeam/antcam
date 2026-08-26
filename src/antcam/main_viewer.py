import logging
import math
import sys
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional, Tuple

from antcam.importer import Model
from antcam.feature_extractor import FeatureExtractor
from antcam.contour_extractor import ContourExtractor
from antcam.planner_config import PlannerRuntimeConfig
import numpy as np

from antcam.path_generator import ToolpathPlan

"""Entry point for loading a model and opening the interactive feature viewer.

This module wires together import, feature extraction, contour extraction, and
viewer presentation. It is intentionally thin and orchestration-focused.
"""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("antcam")

try:
    from antcam.viewer import show_model_with_features
except ImportError:
    show_model_with_features = None


@dataclass(frozen=True)
class ViewerAnalysisBundle:
    model: Model
    features: list[Any]
    diagnostics: dict[str, Any]
    working_plane_normal: Tuple[float, float, float]
    contour_shadow: Any
    perimeter: list[Any]
    vertical_arc_groups: list[Any]
    toolpath_plan: Any
    planner_config: PlannerRuntimeConfig
    roughing_offset_body: Any = None  # 3D offset shape for semi-transparent overlay


def _load_input_model(
    file_path,
    rx=0.0,
    ry=0.0,
    rz=0.0,
    enable_planar_merge: bool = True,
) -> Optional[Model]:
    normalized_path = str(file_path)
    file_extension = normalized_path.lower().split('.')[-1]

    try:
        if file_extension in ['stl']:
            logger.info("Detected STL input; loading mesh-only inspection workflow")
            if not enable_planar_merge:
                logger.info("Experimental planar merge flag disabled")
            model = Model.from_stl(normalized_path)
        elif file_extension in ['step', 'stp']:
            logger.info("Detected STEP input")
            model = Model.from_step(normalized_path, rx=rx, ry=ry, rz=rz)
        else:
            raise ValueError(f"Unsupported file format: .{file_extension}. Use .stl or .step/.stp")

        if not model.brep and not model.mesh:
            raise ValueError("No valid model geometry was loaded")
    except Exception as e:
        logger.error(f"Model loading failed: {e}")
        return None

    return model


def _compute_model_projection_bounds(
    model: Model,
    working_plane_normal: Tuple[float, float, float],
) -> Optional[Tuple[float, float]]:
    brep = getattr(model, "brep", None)
    if brep is None:
        return None

    try:
        from OCP.BRepBndLib import BRepBndLib
        from OCP.Bnd import Bnd_Box
    except ImportError:
        return None

    try:
        nx, ny, nz = (float(v) for v in working_plane_normal)
        normal_norm = math.sqrt(nx * nx + ny * ny + nz * nz)
        if normal_norm <= 1e-9:
            return None
        nx /= normal_norm
        ny /= normal_norm
        nz /= normal_norm

        bbox = Bnd_Box()
        BRepBndLib.Add_s(brep, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        corners = (
            (xmin, ymin, zmin),
            (xmax, ymin, zmin),
            (xmin, ymax, zmin),
            (xmax, ymax, zmin),
            (xmin, ymin, zmax),
            (xmax, ymin, zmax),
            (xmin, ymax, zmax),
            (xmax, ymax, zmax),
        )
        projections = [x * nx + y * ny + z * nz for x, y, z in corners]
        return min(projections), max(projections)
    except Exception as exc:
        logger.warning("Projection-bounds inference failed: %s", exc)
        return None


def _build_contour_profile_preview_plan(
    model: Model,
    perimeter: list[Any],
    working_plane_normal: Tuple[float, float, float],
    planner_config: PlannerRuntimeConfig,
    *,
    planner: Optional[Any] = None,
    single_pass: bool = False,
) -> tuple[ToolpathPlan, list[Any], Optional[Tuple[float, float]]]:
    piece_projection_bounds = _compute_model_projection_bounds(model, working_plane_normal)
    contour_planner = planner
    if contour_planner is None:
        contour_config = replace(planner_config, piece_roughing_only=False, profile_rough_only=False)
        contour_planner = contour_config.build_planner(working_plane_normal)

    contour_operations = []
    preview_loops = []
    supports_profile_preview = all(
        hasattr(contour_planner, attr)
        for attr in ("wires_to_polylines", "_normalize_planar_loops", "_point_at_projection", "_build_profile_operation")
    )
    if supports_profile_preview and perimeter:
        try:
            contour_loops = contour_planner.wires_to_polylines(perimeter)
            preview_loops = contour_planner._normalize_planar_loops(contour_loops) if contour_loops else []
        except Exception:
            preview_loops = []

    contour_top_projection = (
        float(piece_projection_bounds[1])
        if piece_projection_bounds is not None
        else max((float(point[2]) for loop in preview_loops for point in loop), default=0.0)
    )
    contour_safe_projection = contour_top_projection + planner_config.safe_z_offset

    if supports_profile_preview:
        for loop_index, preview_loop in enumerate(preview_loops):
            lifted_preview_loop = [
                contour_planner._point_at_projection(point, contour_top_projection)
                for point in preview_loop
            ]
            contour_operation = contour_planner._build_profile_operation(
                op_id=f"profile_{loop_index}",
                points=lifted_preview_loop,
                safe_projection=contour_safe_projection,
                top_projection=contour_top_projection,
                operation_mode="finishing",
                geometry_source="perimeter_occ_wire_top_projection",
                prefer_smaller_area=loop_index != 0,
                max_passes=1 if single_pass else None,
            )
            if contour_operation is not None:
                contour_operations.append(contour_operation)

    return (
        ToolpathPlan(
            working_plane_normal=tuple(float(v) for v in working_plane_normal),
            safe_projection=float(contour_safe_projection),
            operations=contour_operations,
            warnings=[],
        ),
        preview_loops,
        piece_projection_bounds,
    )


def _log_shape_type(shape: Any, log: logging.Logger, prefix: str = "") -> None:
    """Log the OCC shape type for debugging."""
    try:
        from OCP.TopAbs import TopAbs_ShapeEnum
        st = shape.ShapeType()
        names = {
            TopAbs_ShapeEnum.TopAbs_COMPOUND: "COMPOUND",
            TopAbs_ShapeEnum.TopAbs_COMPSOLID: "COMPSOLID",
            TopAbs_ShapeEnum.TopAbs_SOLID: "SOLID",
            TopAbs_ShapeEnum.TopAbs_SHELL: "SHELL",
            TopAbs_ShapeEnum.TopAbs_FACE: "FACE",
            TopAbs_ShapeEnum.TopAbs_WIRE: "WIRE",
            TopAbs_ShapeEnum.TopAbs_EDGE: "EDGE",
            TopAbs_ShapeEnum.TopAbs_VERTEX: "VERTEX",
        }
        name = names.get(st, str(st))
        log.info("%sShape type: %s", prefix, name)
    except Exception:
        pass


def _perform_offset_attempt(
    shape: Any,
    distance: float,
    mode: Any,
    intersection: bool,
    join_type: Any,
    label: str,
    log: logging.Logger,
) -> Optional[Any]:
    """Single PerformByJoin attempt with detailed logging."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape

    try:
        offset_maker = BRepOffsetAPI_MakeOffsetShape()
        offset_maker.PerformByJoin(
            shape, distance, 1e-3,
            mode, intersection, False,
            join_type, False,
        )
        if offset_maker.IsDone():
            result = offset_maker.Shape()
            if not result.IsNull():
                log.info("Offset OK: %s (dist=%.3f)", label, distance)
                return result
            log.warning("Offset null: %s", label)
        else:
            log.warning("Offset not done: %s", label)
    except Exception as exc:
        log.warning("Offset exception: %s — %s", label, exc)
    return None


def _try_offset_strategies(
    shape: Any,
    distance: float,
    log: logging.Logger,
) -> Optional[Any]:
    """Try all PerformByJoin strategy combinations."""
    from OCP.BRepOffset import BRepOffset_Mode
    from OCP.GeomAbs import GeomAbs_Arc, GeomAbs_Intersection, GeomAbs_Tangent

    strategies = [
        (BRepOffset_Mode.BRepOffset_Skin,       False,         GeomAbs_Arc,         "Skin+Arc+noInter"),
        (BRepOffset_Mode.BRepOffset_Skin,       False,         GeomAbs_Intersection, "Skin+Intersection+noInter"),
        (BRepOffset_Mode.BRepOffset_Pipe,       False,         GeomAbs_Arc,         "Pipe+Arc+noInter"),
        (BRepOffset_Mode.BRepOffset_Skin,       False,         GeomAbs_Tangent,      "Skin+Tangent+noInter"),
        (BRepOffset_Mode.BRepOffset_Skin,       True,          GeomAbs_Arc,         "Skin+Arc+Inter"),
        (BRepOffset_Mode.BRepOffset_Pipe,       True,          GeomAbs_Intersection, "Pipe+Intersection+Inter"),
    ]

    for mode, intersection, join_type, label in strategies:
        result = _perform_offset_attempt(shape, distance, mode, intersection, join_type, label, log)
        if result is not None:
            return result

    return None


def _extract_solid_from_shape(shape: Any) -> Optional[Any]:
    """Extract the first solid from a compound shape."""
    try:
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_SOLID
        from OCP.TopoDS import TopoDS
        exp = TopExp_Explorer(shape, TopAbs_SOLID)
        if exp.More():
            return TopoDS.Solid_s(exp.Current())
    except Exception:
        pass
    return None


def _compute_offset_shape(
    model: Model,
    offset_distance: float,
    log: logging.Logger,
) -> Any:
    """Calcola un corpo offset 3D del modello con catena di fallback.

    Tenta in ordine:
      1. PerformByJoin con varie combinazioni (Intersection=False prima)
      2. Se shape e' Compound, estrae Solid e riprova
      3. Offset per-slice 2D con loft (fino a 20 slice, offset polilinea)
      4. Bounding box espansa (fallback finale con WARNING)
    """
    model_brep = getattr(model, "brep", None)
    if model_brep is None:
        return None

    _log_shape_type(model_brep, log)
    log.info("Offset distance: %.4f", offset_distance)

    # --- 1. Direct PerformByJoin attempts ---
    result = _try_offset_strategies(model_brep, offset_distance, log)
    if result is not None:
        return result

    # --- 2. Try extracting solid from compound ---
    solid = _extract_solid_from_shape(model_brep)
    if solid is not None:
        log.info("Shape is compound, extracted Solid — retrying offset strategies")
        result = _try_offset_strategies(solid, offset_distance, log)
        if result is not None:
            return result

    # --- 3. Improved slice-loft fallback ---
    log.warning("Offset 3D OCC fallito, tento slice-loft con offset polilinea")
    try:
        shape = _build_slice_loft_offset(model_brep, offset_distance, log)
        if shape is not None and not shape.IsNull():
            log.info("Slice-loft offset OK")
            return shape
    except Exception as exc:
        log.warning("Offset slice-loft fallito: %s", exc)

    # --- 4. Bounding box fallback (ULTIMA risorsa, logga WARNING) ---
    log.warning("TUTTI gli offset 3D falliti! Uso bbox espansa come FALLBACK.")
    log.warning("Il path di sgrossatura usera' l'offset 2D per-Z invece dell'offset 3D.")
    try:
        shape = _build_bbox_offset(model_brep, offset_distance, log)
        if shape is not None and not shape.IsNull():
            log.info("BBox offset fallback OK")
            return shape
    except Exception as exc:
        log.warning("Offset bbox fallito: %s", exc)

    log.error("Nessun offset body disponibile")
    return None


def _polyline_offset_2d(
    points_2d: list[np.ndarray],
    distance: float,
) -> list[np.ndarray]:
    """Offset a 2D closed polyline outward (+distance) or inward (-distance)
    using vertex-normal intersection (Minkowski-like)."""
    n = len(points_2d)
    if n < 3:
        return list(points_2d)

    offset: list[np.ndarray] = []
    for i in range(n):
        prev = points_2d[(i - 1) % n]
        curr = points_2d[i]
        next_ = points_2d[(i + 1) % n]

        d1 = curr - prev
        d2 = next_ - curr
        n1 = float(np.linalg.norm(d1))
        n2 = float(np.linalg.norm(d2))
        if n1 < 1e-9 or n2 < 1e-9:
            continue
        d1 /= n1
        d2 /= n2

        n1_perp = np.array([-d1[1], d1[0]])
        n2_perp = np.array([-d2[1], d2[0]])

        p1 = curr + n1_perp * distance
        p2 = curr + n2_perp * distance

        A = np.column_stack((d1, -d2))
        try:
            ts = np.linalg.solve(A, p2 - p1)
        except np.linalg.LinAlgError:
            continue

        pt = p1 + ts[0] * d1
        if np.isfinite(pt).all() and float(np.linalg.norm(pt - curr)) < abs(distance) * 10.0:
            offset.append(pt)
        else:
            bisector = n1_perp + n2_perp
            bn = float(np.linalg.norm(bisector))
            if bn < 1e-9:
                bisector = n1_perp
                bn = float(np.linalg.norm(bisector))
            if bn < 1e-9:
                continue
            bisector /= bn
            denom = max(abs(float(np.dot(bisector, n1_perp))), 0.25)
            offset.append(curr + bisector * (distance / denom))

    return offset if len(offset) >= 3 else list(points_2d)


def _build_slice_loft_offset(brep: Any, distance: float, log: logging.Logger) -> Any:
    """Offset per-slice 2D via OCC section + BBox classification.

    - Seziona BRep a piu` Z level via BRepAlgoAPI_Section → TopoDS_Wire
    - Classifica per BBox containment (outer/holes)
    - Offsetta l'outer di ogni slice via BRepOffsetAPI_MakeOffset
    - Loft tra le slice offsettate
    - Fallback su mesh slicing se OCC section fallisce
    """
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_Box
    from OCP.gp import gp_Pnt
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopoDS import TopoDS
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset, BRepOffsetAPI_ThruSections
    from OCP.GeomAbs import GeomAbs_Intersection
    from OCP.BRepLib import BRepLib

    from antcam.slicer import (
        _brep_section_get_wires,
        _brep_section_classify_wires,
        _tessellate_brep,
        _slice_mesh_at_z,
        _loop_signed_area_xy,
    )

    bbox = Bnd_Box()
    BRepBndLib.Add_s(brep, bbox)
    x_min, y_min, z_min, x_max, y_max, z_max = bbox.Get()
    height = z_max - z_min
    if height < 1e-6:
        raise RuntimeError("Part height too small for slice-loft")

    n_slices = max(8, min(40, int(height / 3.0)))
    z_levels = [z_min + height * (i + 0.5) / n_slices for i in range(n_slices)]
    log.info("Slice-loft: %d slices su %.1fmm", n_slices, height)

    w = (0.0, 0.0, 1.0)

    def _poly_to_wire(points):
        if len(points) < 3:
            return None
        polygon = BRepBuilderAPI_MakePolygon()
        for p in points:
            polygon.Add(gp_Pnt(float(p[0]), float(p[1]), float(p[2])))
        polygon.Close()
        try:
            return polygon.Wire()
        except Exception:
            return None

    slice_offset_wires: list[Any] = []
    mesh_fallback = False

    for z_level in z_levels:
        outer_wire = None
        try:
            wires = _brep_section_get_wires(brep, z_level, w)
            if wires:
                groups = _brep_section_classify_wires(wires)
                if groups:
                    outer_wire = groups[0][0]
        except Exception:
            pass

        if outer_wire is None:
            # Fallback: mesh slicing
            if not mesh_fallback:
                mesh = _tessellate_brep(brep, 0.02)
                if mesh is None or mesh.is_empty:
                    continue
                mesh_fallback = True
            polylines = _slice_mesh_at_z(mesh, z_level, w)
            if not polylines:
                continue
            polyline = max(polylines, key=lambda p: abs(_loop_signed_area_xy(p)))
            pts_2d = [np.array([p[0], p[1]], dtype=float) for p in polyline]

            occ_ok = False
            wire = _poly_to_wire(polyline)
            if wire is not None:
                try:
                    off_builder = BRepOffsetAPI_MakeOffset()
                    off_builder.Init(GeomAbs_Intersection)
                    off_builder.AddWire(wire)
                    off_builder.Perform(distance, 0.0)
                    off_shape = off_builder.Shape()
                    if not off_shape.IsNull():
                        exp_w = TopExp_Explorer(off_shape, TopAbs_WIRE)
                        while exp_w.More():
                            slice_offset_wires.append(TopoDS.Wire_s(exp_w.Current()))
                            occ_ok = True
                            exp_w.Next()
                except Exception:
                    pass

            if occ_ok:
                continue

            off_2d = _polyline_offset_2d(pts_2d, distance)
            if len(off_2d) < 3:
                continue
            off_wire = _poly_to_wire([(float(p[0]), float(p[1]), float(z_level)) for p in off_2d])
            if off_wire is not None:
                slice_offset_wires.append(off_wire)
        else:
            try:
                off_builder = BRepOffsetAPI_MakeOffset()
                off_builder.Init(GeomAbs_Intersection)
                off_builder.AddWire(outer_wire)
                off_builder.Perform(distance, 0.0)
                off_shape = off_builder.Shape()
                if not off_shape.IsNull():
                    exp_w = TopExp_Explorer(off_shape, TopAbs_WIRE)
                    while exp_w.More():
                        slice_offset_wires.append(TopoDS.Wire_s(exp_w.Current()))
                        exp_w.Next()
            except Exception:
                continue

    if len(slice_offset_wires) < 2:
        raise RuntimeError(f"Solo {len(slice_offset_wires)} slice offset (necessario >=2)")

    log.info("Slice-loft: %d wires per loft", len(slice_offset_wires))
    loft = BRepOffsetAPI_ThruSections(False, True, 1e-2)
    for w in slice_offset_wires:
        loft.AddWire(w)
    loft.Build()
    if loft.IsDone():
        shape = loft.Shape()
        BRepLib.SewShape_s(shape, 1e-3)
        return shape

    raise RuntimeError("Slice loft non riuscito")


def _build_bbox_offset(brep: Any, distance: float, log: logging.Logger) -> Any:
    """Crea una semplice scatola espansa attorno al pezzo come fallback offset."""
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_MakeShell
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    bbox = Bnd_Box()
    BRepBndLib.Add_s(brep, bbox)
    x_min, y_min, z_min, x_max, y_max, z_max = bbox.Get()

    box = BRepPrimAPI_MakeBox(
        gp_Pnt(x_min - distance, y_min - distance, z_min - distance),
        gp_Pnt(x_max + distance, y_max + distance, z_max + distance),
    ).Shape()
    log.info("BBox offset creato: distanza=%s", distance)
    return box


def build_viewer_analysis(
    file_path,
    rx=0.0,
    ry=0.0,
    rz=0.0,
    enable_planar_merge: bool = True,
    planner_config: Optional[PlannerRuntimeConfig] = None,
) -> Optional[ViewerAnalysisBundle]:
    """Load a CAD file and compute the shared analysis bundle used by viewers."""
    logger.info("Starting model analysis for %s", file_path)
    planner_config = planner_config or PlannerRuntimeConfig()

    model = _load_input_model(
        file_path,
        rx=rx,
        ry=ry,
        rz=rz,
        enable_planar_merge=enable_planar_merge,
    )
    if model is None:
        return None

    extractor = FeatureExtractor(model)
    features = extractor.extract()
    diagnostics = getattr(extractor, "diagnostics", {})
    vertical_arc_groups = getattr(extractor, "_arc_groups", None)
    if vertical_arc_groups is None:
        vertical_arc_groups = extractor.find_vertical_faces_with_xy_arcs()
    hole_groups = [f for f in features if getattr(f, "type", "") == "hole_group"]
    through_hole_groups = [f for f in hole_groups if f.props.get("through")]
    if diagnostics:
        raw_candidates = diagnostics.get("raw_candidates", {})
        logger.info(
            "Recognition diagnostics: cylinders=%d, cones=%d, arc_groups=%d, features=%d",
            raw_candidates.get("cylinders", 0),
            raw_candidates.get("cones", 0),
            raw_candidates.get("arc_groups", 0),
            diagnostics.get("feature_count", len(features)),
        )
    logger.info(
        "Hole diagnostics: hole_group=%d, through=%d, arc_groups=%d",
        len(hole_groups),
        len(through_hole_groups),
        len(vertical_arc_groups),
    )
    logger.info(
        "Planner config: mode=%s, tool_library=%s%s, material_profile=%s%s, tool_diameter=%.3f, profile_stock_allowance=%.3f, drill_tool=%s, mill_tool=%s",
        planner_config.parameter_mode,
        planner_config.tool_library,
        f" ({planner_config.tool_library_file})" if planner_config.tool_library_file else "",
        planner_config.material_profile,
        f" ({planner_config.material_profile_file})" if planner_config.material_profile_file else "",
        planner_config.tool_diameter,
        planner_config.profile_stock_allowance,
        planner_config.drill_tool_id or "auto",
        planner_config.mill_tool_id or "auto",
    )
    logger.info("Detected %d features:", len(features))
    for f in features:
        logger.info(f"  - {f}")

    # Normalize to plain floats for stable downstream serialization/logging.
    working_plane_normal: Tuple[float, float, float] = tuple(float(v) for v in extractor.working_plane_normal)
    contour_extractor = ContourExtractor(model, working_plane_normal=working_plane_normal, features=features)
    contour_shadow = contour_extractor.extract()
    perimeter = contour_extractor.extract_perimeter(contour_shadow) if contour_shadow is not None else []

    planner = planner_config.build_planner(working_plane_normal)
    toolpath_plan = planner.generate(features=features, perimeter_wires=perimeter)
    drill_ops = sum(1 for op in toolpath_plan.operations if op.strategy == "drilling")
    slot_ops = sum(1 for op in toolpath_plan.operations if op.strategy == "slot_milling")
    cavity_ops = sum(1 for op in toolpath_plan.operations if op.strategy == "cavity_clearing")
    profile_ops = sum(1 for op in toolpath_plan.operations if op.strategy == "2p5d_profile")
    logger.info(
        "Toolpath plan: operations=%d (drilling=%d, slot=%d, cavity=%d, profile=%d) warnings=%d",
        len(toolpath_plan.operations),
        drill_ops,
        slot_ops,
        cavity_ops,
        profile_ops,
        len(toolpath_plan.warnings),
    )

    return ViewerAnalysisBundle(
        model=model,
        features=features,
        diagnostics=diagnostics,
        working_plane_normal=working_plane_normal,
        contour_shadow=contour_shadow,
        perimeter=perimeter,
        vertical_arc_groups=vertical_arc_groups,
        toolpath_plan=toolpath_plan,
        planner_config=planner_config,
    )


def build_piece_roughing_analysis(
    file_path,
    rx=0.0,
    ry=0.0,
    rz=0.0,
    enable_planar_merge: bool = True,
    planner_config: Optional[PlannerRuntimeConfig] = None,
) -> Optional[ViewerAnalysisBundle]:
    """Load a CAD file and compute a perimeter-only roughing bundle.

    This path intentionally skips semantic feature extraction so the first roughing
    baseline depends only on the part silhouette/perimeter.
    """
    logger.info("Starting piece-based roughing analysis for %s", file_path)
    planner_config = planner_config or PlannerRuntimeConfig(piece_roughing_only=True)

    model = _load_input_model(
        file_path,
        rx=rx,
        ry=ry,
        rz=rz,
        enable_planar_merge=enable_planar_merge,
    )
    if model is None:
        return None

    working_plane_normal: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    piece_projection_bounds = _compute_model_projection_bounds(model, working_plane_normal)
    contour_extractor = ContourExtractor(model, working_plane_normal=working_plane_normal, features=[])
    contour_shadow = contour_extractor.extract()
    perimeter = contour_extractor.extract_perimeter(contour_shadow) if contour_shadow is not None else []

    planner = planner_config.build_planner(working_plane_normal)
    toolpath_plan = planner.generate(
        features=[],
        perimeter_wires=perimeter,
        piece_top_projection=piece_projection_bounds[1] if piece_projection_bounds is not None else None,
        piece_bottom_projection=piece_projection_bounds[0] if piece_projection_bounds is not None else None,
    )
    drill_ops = sum(1 for op in toolpath_plan.operations if op.strategy == "drilling")
    slot_ops = sum(1 for op in toolpath_plan.operations if op.strategy == "slot_milling")
    cavity_ops = sum(1 for op in toolpath_plan.operations if op.strategy == "cavity_clearing")
    profile_ops = sum(1 for op in toolpath_plan.operations if op.strategy == "2p5d_profile")
    logger.info(
        "Toolpath plan: operations=%d (drilling=%d, slot=%d, cavity=%d, profile=%d) warnings=%d",
        len(toolpath_plan.operations),
        drill_ops,
        slot_ops,
        cavity_ops,
        profile_ops,
        len(toolpath_plan.warnings),
    )

    diagnostics = {
        "has_brep": bool(model.brep),
        "skip_reason": "piece_roughing_only",
        "raw_candidates": {"cylinders": 0, "cones": 0, "arc_groups": 0},
        "pre_group_feature_count": 0,
        "feature_count": 0,
        "feature_types": {},
    }
    if piece_projection_bounds is not None:
        diagnostics["piece_projection_bounds"] = {
            "bottom": round(float(piece_projection_bounds[0]), 4),
            "top": round(float(piece_projection_bounds[1]), 4),
            "depth": round(float(piece_projection_bounds[1] - piece_projection_bounds[0]), 4),
        }

    return ViewerAnalysisBundle(
        model=model,
        features=[],
        diagnostics=diagnostics,
        working_plane_normal=working_plane_normal,
        contour_shadow=contour_shadow,
        perimeter=perimeter,
        vertical_arc_groups=[],
        toolpath_plan=toolpath_plan,
        planner_config=planner_config,
    )


def run_feature_viewer(
    file_path,
    rx=0.0,
    ry=0.0,
    rz=0.0,
    enable_planar_merge: bool = True,
    planner_config: Optional[PlannerRuntimeConfig] = None,
    show_viewer: Optional[Callable[..., Any]] = None,
):
    """Load a CAD file, extract analysis data, and launch the viewer.

    The function supports STEP/STP and STL input. For STEP/STP, optional
    Euler rotations can be applied before extraction. For STL, the current
    pipeline runs in mesh mode and gracefully skips BRep-only operations
    downstream when no BRep is available.

    Args:
        file_path: Input file path.
        rx: Rotation around X axis (degrees), STEP/STP only.
        ry: Rotation around Y axis (degrees), STEP/STP only.
        rz: Rotation around Z axis (degrees), STEP/STP only.
        enable_planar_merge: Reserved compatibility flag for STL import path.
        planner_config: External planner settings shared with CLI and launcher.
    """
    viewer_callable = show_viewer or show_model_with_features
    if viewer_callable is None:
        logger.info("Starting model analysis for %s", file_path)
        _load_input_model(
            file_path,
            rx=rx,
            ry=ry,
            rz=rz,
            enable_planar_merge=enable_planar_merge,
        )
        logger.error("Viewer dependencies are unavailable. Install the 'gui' extra to use the interactive viewer.")
        return

    analysis = build_viewer_analysis(
        file_path,
        rx=rx,
        ry=ry,
        rz=rz,
        enable_planar_merge=enable_planar_merge,
        planner_config=planner_config,
    )
    if analysis is None:
        return

    viewer_callable(
        analysis.model,
        analysis.features,
        working_plane_normal=analysis.working_plane_normal,
        contour_shadow=analysis.contour_shadow,
        perimeter=analysis.perimeter,
        vertical_arc_groups=analysis.vertical_arc_groups,
        toolpath_plan=analysis.toolpath_plan,
    )


def run_piece_viewer(
    file_path,
    rx=0.0,
    ry=0.0,
    rz=0.0,
    enable_planar_merge: bool = True,
    show_viewer: Optional[Callable[..., Any]] = None,
):
    """Load a CAD file and launch a viewer that renders only the solid piece."""
    viewer_callable = show_viewer or show_model_with_features
    if viewer_callable is None:
        logger.info("Starting piece-only model view for %s", file_path)
        _load_input_model(
            file_path,
            rx=rx,
            ry=ry,
            rz=rz,
            enable_planar_merge=enable_planar_merge,
        )
        logger.error("Viewer dependencies are unavailable. Install the 'gui' extra to use the interactive viewer.")
        return

    model = _load_input_model(
        file_path,
        rx=rx,
        ry=ry,
        rz=rz,
        enable_planar_merge=enable_planar_merge,
    )
    if model is None:
        return

    working_plane_normal: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    contour_extractor = ContourExtractor(model, working_plane_normal=working_plane_normal, features=[])
    contour_shadow = contour_extractor.extract()
    perimeter = contour_extractor.extract_perimeter(contour_shadow) if contour_shadow is not None else []

    viewer_callable(
        model,
        [],
        working_plane_normal=working_plane_normal,
        contour_shadow=contour_shadow,
        perimeter=perimeter,
        vertical_arc_groups=[],
        toolpath_plan=None,
        enable_face_selection=False,
        show_toolpath_legend=False,
        show_model_edges=True,
        status_message="Piece viewer | modello solido opaco + profilo ombra",
    )


def build_piece_toolpath_preview_analysis(
    file_path,
    rx=0.0,
    ry=0.0,
    rz=0.0,
    enable_planar_merge: bool = True,
    planner_config: Optional[PlannerRuntimeConfig] = None,
) -> Optional[ViewerAnalysisBundle]:
    """Load a CAD file and compute a contour-only preview bundle for the piece."""
    logger.info("Starting piece toolpath preview analysis for %s", file_path)
    planner_config = planner_config or PlannerRuntimeConfig()

    model = _load_input_model(
        file_path,
        rx=rx,
        ry=ry,
        rz=rz,
        enable_planar_merge=enable_planar_merge,
    )
    if model is None:
        return None

    working_plane_normal: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    piece_projection_bounds = _compute_model_projection_bounds(model, working_plane_normal)
    contour_extractor = ContourExtractor(model, working_plane_normal=working_plane_normal, features=[])
    contour_shadow = contour_extractor.extract()
    perimeter = contour_extractor.extract_perimeter(contour_shadow) if contour_shadow is not None else []

    contour_config = replace(planner_config, piece_roughing_only=False, profile_rough_only=False)
    contour_planner = contour_config.build_planner(working_plane_normal)
    roughing_config = replace(planner_config, piece_roughing_only=True, profile_rough_only=False)
    roughing_planner = roughing_config.build_planner(working_plane_normal)
    roughing_plan = roughing_planner.generate(
        features=[],
        perimeter_wires=perimeter,
        piece_top_projection=piece_projection_bounds[1] if piece_projection_bounds is not None else None,
        piece_bottom_projection=piece_projection_bounds[0] if piece_projection_bounds is not None else None,
    )
    contour_plan, preview_loops, piece_projection_bounds = _build_contour_profile_preview_plan(
        model,
        perimeter,
        working_plane_normal,
        contour_config,
        planner=contour_planner,
        single_pass=True,
    )

    roughing_offset_body = _compute_offset_shape(
        model, float(planner_config.roughing_distance), logger,
    )

    merged_plan = ToolpathPlan(
        working_plane_normal=tuple(float(v) for v in getattr(contour_plan, "working_plane_normal", working_plane_normal)),
        safe_projection=max(
            float(getattr(roughing_plan, "safe_projection", planner_config.safe_z_offset)),
            float(getattr(contour_plan, "safe_projection", 0.0)),
        ),
        operations=list(getattr(roughing_plan, "operations", [])) + list(getattr(contour_plan, "operations", [])),
        warnings=list(getattr(roughing_plan, "warnings", []) or []),
    )

    diagnostics = {
        "has_brep": bool(model.brep),
        "has_roughing_offset": roughing_offset_body is not None,
        "roughing_distance": planner_config.roughing_distance,
        "skip_reason": "piece_toolpath_preview",
        "raw_candidates": {"cylinders": 0, "cones": 0, "arc_groups": 0},
        "pre_group_feature_count": 0,
        "feature_count": 0,
        "feature_types": {},
        "toolpath_preview": {
            "roughing_operations": len(getattr(roughing_plan, "operations", [])),
            "contour_operations": len(getattr(contour_plan, "operations", [])),
            "contour_preview_loops": len(preview_loops),
        },
    }
    if piece_projection_bounds is not None:
        diagnostics["piece_projection_bounds"] = {
            "bottom": round(float(piece_projection_bounds[0]), 4),
            "top": round(float(piece_projection_bounds[1]), 4),
            "depth": round(float(piece_projection_bounds[1] - piece_projection_bounds[0]), 4),
        }

    return ViewerAnalysisBundle(
        model=model,
        features=[],
        diagnostics=diagnostics,
        working_plane_normal=working_plane_normal,
        contour_shadow=contour_shadow,
        perimeter=perimeter,
        vertical_arc_groups=[],
        toolpath_plan=merged_plan,
        planner_config=planner_config,
        roughing_offset_body=roughing_offset_body,
    )


def run_piece_toolpath_preview(
    file_path,
    rx=0.0,
    ry=0.0,
    rz=0.0,
    enable_planar_merge: bool = True,
    planner_config: Optional[PlannerRuntimeConfig] = None,
    show_viewer: Optional[Callable[..., Any]] = None,
    offset_distance: float = 1.0,
):
    """Load a CAD file and launch a piece-based contour/rough preview viewer."""
    if show_viewer is None:
        try:
            from antcam.piece_toolpath_viewer import show_piece_toolpath_preview
        except ImportError:
            show_piece_toolpath_preview = None
        viewer_callable = show_piece_toolpath_preview
    else:
        viewer_callable = show_viewer

    if viewer_callable is None:
        logger.info("Starting piece toolpath preview for %s", file_path)
        _load_input_model(
            file_path,
            rx=rx,
            ry=ry,
            rz=rz,
            enable_planar_merge=enable_planar_merge,
        )
        logger.error("Viewer dependencies are unavailable. Install the 'gui' extra to use the interactive viewer.")
        return

    analysis = build_piece_toolpath_preview_analysis(
        file_path,
        rx=rx,
        ry=ry,
        rz=rz,
        enable_planar_merge=enable_planar_merge,
        planner_config=planner_config,
    )
    if analysis is None:
        return

    viewer_callable(
        analysis.model,
        analysis.features,
        working_plane_normal=analysis.working_plane_normal,
        contour_shadow=analysis.contour_shadow,
        perimeter=analysis.perimeter,
        vertical_arc_groups=analysis.vertical_arc_groups,
        toolpath_plan=analysis.toolpath_plan,
        diagnostics=analysis.diagnostics,
        planner_config=analysis.planner_config,
        roughing_offset_body=analysis.roughing_offset_body,
        offset_distance=offset_distance,
    )


MODELS = [
    # STEP files (native CAD geometry)
    ("../../tests/data/bottle_opener.step", 0.0, 0.0, 0.0),
    ("../../tests/data/flange.step",        0.0, 0.0, 0.0),
    ("../../tests/data/mounting_spider.step", 0.0, 0.0, 0.0),
    ("../../tests/data/servo_mount.step",   90.0, 0.0, 0.0),
    # STL files (mesh-only inspection path)
    ("../../tests/data/MALE_BUCKLE.stl",   0.0, 0.0, 0.0),
]

if __name__ == "__main__":
    path, rx, ry, rz = MODELS[2]
    if len(sys.argv) > 1:
        path = sys.argv[1]
    run_feature_viewer(path, rx=rx, ry=ry, rz=rz)
