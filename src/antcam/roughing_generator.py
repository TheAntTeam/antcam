"""Professional Z-level roughing generator.

Strategy (per-Z 2D offset):
  1. Section the ORIGINAL part at each Z level → part contour
  2. 2D-offset the part contour outward by (roughing_distance + tool_radius) → safe outer boundary
  3. Use part contour as inner keep-out island
  4. Fill the annular region with contour-parallel (preferred) or raster (fallback)
  5. Helical ramp between levels

This approach avoids the 3D offset computation entirely for the path generator.
The 3D offset is only used for the orange display overlay.

Usage (incremental - recommended for UI responsiveness):
    gen = RoughingGenerator(planner, roughing_distance, tool_diameter)
    gen.prepare(original_brep, safe_z, top_z, bottom_z, params)
    while not gen.done:
        op = gen.compute_next()
        if op: plan.operations.insert(0, op)

Usage (batch):
    ops = gen.generate(...)
"""

from __future__ import annotations

import math
import logging
from typing import Any, Callable, Optional

import numpy as np

from antcam.path_generator import (
    AutoToolpathPlanner,
    MachiningParameters,
    MotionCommand,
    PlanarRegion,
    ToolpathOperation,
)

ProgressCallback = Callable[[int, int, str], None]

logger = logging.getLogger("antcam")


class RoughingGenerator:
    """Generate Z-level roughing toolpaths from a 3D offset surface."""

    def __init__(
        self,
        planner: AutoToolpathPlanner,
        roughing_distance: float = 1.0,
        tool_diameter: float = 6.0,
        stepover_ratio: float = 0.6,
        helical_turns: int = 2,
        clearance_z: float = 1.0,
    ) -> None:
        self._planner = planner
        self._roughing_distance = float(roughing_distance)
        self._tool_diameter = float(tool_diameter)
        self._tool_radius = self._tool_diameter * 0.5
        self._stepover_ratio = float(stepover_ratio)
        self._helical_turns = max(1, int(helical_turns))
        self._clearance_z = float(clearance_z)
        self._offset_distance = self._roughing_distance + self._tool_radius

        # Incremental state (set by prepare())
        self._state: Optional[dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Incremental API
    # ------------------------------------------------------------------

    @property
    def total_levels(self) -> int:
        if self._state is None:
            return 0
        return len(self._state["pass_projections"])

    @property
    def current_level(self) -> int:
        if self._state is None:
            return 0
        return self._state["pass_idx"]

    @property
    def done(self) -> bool:
        return self._state is None or self._state["pass_idx"] >= len(self._state["pass_projections"])

    def prepare(
        self,
        original_brep: Any,
        safe_projection: float,
        top_projection: float,
        target_projection: float,
        parameters: MachiningParameters,
    ) -> bool:
        """Compute Z-level projections. Must be called before compute_next().

        Returns True if ready for incremental processing.
        No 3D offset is computed here — offset is done per-Z via 2D polyline.
        """
        depth = float(top_projection - target_projection)
        if depth <= 1e-6:
            logger.warning("Roughing: depth too small (%s)", depth)
            return False

        pass_projections = self._planner._build_depth_pass_projections(
            top_projection, target_projection,
            max_stepdown=parameters.max_stepdown,
        )
        if not pass_projections:
            logger.warning("Roughing: no Z level projections")
            return False

        self._state = {
            "original_brep": original_brep,
            "pass_projections": pass_projections,
            "pass_idx": 0,
            "prev_pass_proj": None,
            "parameters": parameters,
            "safe_projection": safe_projection,
            "top_projection": top_projection,
            "target_projection": target_projection,
        }
        logger.info("Roughing prepared: %d Z levels (per-Z 2D offset)", len(pass_projections))
        return True

    def compute_next(self) -> Optional[ToolpathOperation]:
        """Process the next Z level and return its ToolpathOperation.

        Returns None when all levels are done or if the level is empty.
        """
        if self.done:
            return None

        st = self._state
        pass_idx = st["pass_idx"]
        pass_proj = st["pass_projections"][pass_idx]
        params = st["parameters"]
        prev_pass_proj = st["prev_pass_proj"]

        op = self._compute_one_level(
            pass_idx, pass_proj, prev_pass_proj, params,
            st["original_brep"],
        )

        st["pass_idx"] += 1
        if op is not None or prev_pass_proj != pass_proj:
            st["prev_pass_proj"] = pass_proj

        return op

    # ------------------------------------------------------------------
    # Batch API (wraps incremental)
    # ------------------------------------------------------------------

    def generate(
        self,
        original_brep: Any,
        safe_projection: float,
        top_projection: float,
        target_projection: float,
        parameters: MachiningParameters,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> list[ToolpathOperation]:
        """Generate all roughing operations in one batch call.

        For UI responsiveness, use prepare() + compute_next() instead.
        """
        if not self.prepare(original_brep, safe_projection, top_projection, target_projection, parameters):
            return []

        total = self.total_levels
        ops: list[ToolpathOperation] = []

        while not self.done:
            idx = self.current_level + 1
            if progress_callback:
                progress_callback(idx, total, f"Z level {idx}/{total}...")
            op = self.compute_next()
            if op is not None:
                ops.append(op)
            if progress_callback:
                progress_callback(min(idx + 1, total), total,
                                  f"Z level {idx}/{total} OK" if op else f"Z level {idx}/{total} vuoto")

        if progress_callback:
            progress_callback(total, total, f"Sgrossatura completata: {len(ops)} Z-level")
        return ops

    # ------------------------------------------------------------------
    # One-level computation
    # ------------------------------------------------------------------

    def _compute_one_level(
        self,
        pass_idx: int,
        pass_proj: float,
        prev_pass_proj: Optional[float],
        parameters: MachiningParameters,
        original_brep: Any,
    ) -> Optional[ToolpathOperation]:
        """Compute toolpath for a single Z level using per-Z 2D offset.

        1. Section original part at this Z → part contour
        2. 2D-offset part contour outward by _offset_distance → safe outer boundary
        3. Use part contours as inner islands → annular region
        4. Fill region with contour-parallel or raster
        """
        logger.info("Roughing Z-level %d @ Z=%.3f", pass_idx, pass_proj)

        # 1. Section the ORIGINAL part at this Z level
        part_section_loops = self._slice_brep_at_z(original_brep, pass_proj)
        if not part_section_loops:
            logger.info("  nessuna sezione pezzo a Z=%.3f", pass_proj)
            return None

        # 2. Normalize and filter tiny loops
        part_loops = self._planner._normalize_planar_loops(part_section_loops)
        if not part_loops:
            logger.info("  normalize ha prodotto 0 loop")
            return None

        min_area = 0.01 * math.pi * self._tool_radius * self._tool_radius
        part_loops = [lp for lp in part_loops
                      if abs(self._planner._get_loop_signed_area(lp)) > min_area]
        if not part_loops:
            logger.info("  tutti i loop sotto area minima %.4f", min_area)
            return None

        # 3. Sort by area descending: part_loops[0] = outer part contour
        part_loops.sort(key=lambda lp: abs(self._planner._get_loop_signed_area(lp)), reverse=True)
        logger.info("  %d loop pezzo validi (max area=%.2f)",
                     len(part_loops),
                     abs(self._planner._get_loop_signed_area(part_loops[0])))

        # 4. 2D-offset the OUTER part contour outward → safe boundary for tool center
        # Uses cached OCC TopoDS_Wire for direct offset (no polyline roundtrip).
        # BRepOffsetAPI_MakeOffset offsets LEFT of edge direction:
        #   CCW (pos area) → left=outward (+dist expands)
        #   CW  (neg area) → left=inward (+dist contracts)
        # Polylines from MakePolygon + offset degrade geometry → use OCC wire.
        wire_groups = getattr(self._planner, '_section_wire_groups', {}).get(pass_proj)
        outer_offset_wires: list[list[tuple[float, float, float]]] = []
        if wire_groups:
            outer_wire, _hole_wires = wire_groups[0]
            outer_off = self._planner._offset_occ_wire_direct(
                outer_wire, self._offset_distance,
            )
            for off_wire in outer_off:
                from antcam.slicer import _brep_wire_to_polyline
                pts = _brep_wire_to_polyline(off_wire)
                if len(pts) >= 3:
                    outer_offset_wires.append(pts)
        if not outer_offset_wires:
            # Fallback: polyline offset (signed distance, no reversal)
            outer_safe = list(part_loops[0]).copy()
            outer_signed = self._planner._get_loop_signed_area(outer_safe)
            signed_dist = self._offset_distance if outer_signed >= 0 else -self._offset_distance
            outer_safe_offsets = self._planner._offset_planar_loops_occ(
                outer_safe, signed_dist,
            )
            if outer_safe_offsets:
                outer_safe_offsets.sort(
                    key=lambda lp: abs(self._planner._get_loop_signed_area(lp)), reverse=True,
                )
                outer_offset_wires = [outer_safe_offsets[0]]
            else:
                logger.info("  offset 2D fallito, passo vuoto")
                return None

        outer_boundary = outer_offset_wires[0]

        # 5. Build annular region: outer_boundary + all part loops as islands
        all_loops = [outer_boundary] + part_loops
        logger.info("  regione anulare: outer=%.2f + %d isole",
                     abs(self._planner._get_loop_signed_area(outer_boundary)),
                     len(part_loops))

        basis_u, basis_v = self._planner._get_working_plane_basis()
        region = PlanarRegion(
            center=self._compute_region_center(all_loops),
            basis_u=basis_u,
            basis_v=basis_v,
            span_u=self._compute_loop_span(outer_boundary, basis_u),
            span_v=self._compute_loop_span(outer_boundary, basis_v),
            projection=pass_proj,
            boundary_loops=all_loops,
        )

        # 6. Fill region (annular: outer safe boundary - original part as island)
        cut_paths, fill_style = self._fill_region(region, parameters)
        if not cut_paths:
            logger.info("  fill vuoto (stile=%s)", fill_style)
            return None
        logger.info("  fill OK: %d path, stile=%s", len(cut_paths), fill_style)

        # 7. For each hole, generate pocket paths inside it using direct
        # OCC wire offset (bypasses polyline roundtrip which fails MakeOffset).
        from antcam.slicer import _brep_wire_to_polyline

        hole_wires_from_cache = wire_groups[0][1] if wire_groups else []
        hole_path_count = 0
        stepover = float(self._tool_diameter * self._stepover_ratio)
        for hole_idx, hole_wire in enumerate(hole_wires_from_cache, 1):
            hole_polylines: list[list[tuple[float, float, float]]] = []
            radial = -float(self._tool_radius)
            for _ in range(128):
                off = self._planner._offset_occ_wire_direct(hole_wire, radial)
                if not off:
                    break
                for ow in off:
                    pts = _brep_wire_to_polyline(ow)
                    if len(pts) >= 3:
                        hole_polylines.append(pts)
                radial -= stepover  # more negative = deeper inward
            if hole_polylines:
                cut_paths.extend(hole_polylines)
                hole_path_count += len(hole_polylines)
                logger.debug("  foro %d: %d path", hole_idx, len(hole_polylines))
            else:
                logger.debug("  foro %d: nessun path (troppo piccolo?)", hole_idx)

        if hole_path_count:
            logger.info("  + %d path fori aggiunti", hole_path_count)

        motions = self._build_pass_motions(
            cut_paths, pass_proj, parameters,
            prev_pass_proj,
        )
        if not motions:
            return None

        metadata: dict[str, Any] = {
            "pass_index": pass_idx,
            "pass_projection": round(float(pass_proj), 4),
            "path_count": len(cut_paths),
            "section_loop_count": len(part_section_loops),
            "part_loop_count": len(part_loops),
            "fill_style": fill_style,
            "roughing_distance": round(self._roughing_distance, 4),
            "offset_distance": round(self._offset_distance, 4),
            "helical_turns": self._helical_turns if prev_pass_proj is not None else 0,
            "span_u": round(region.span_u, 4),
            "span_v": round(region.span_v, 4),
        }
        metadata.update(
            self._planner._build_feature_metadata(
                self._planner._build_perimeter_context(
                    closed=True, geometry_source="roughing_2d_offset",
                )
            )
        )
        metadata.update(self._planner._build_parameter_metadata(parameters, None))
        metadata["operation_mode"] = "roughing"

        return ToolpathOperation(
            op_id=f"roughing_z_{pass_idx:03d}",
            strategy="cavity_clearing",
            feature_type="piece",
            motions=motions,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Section helpers
    # ------------------------------------------------------------------

    def _slice_brep_at_z(self, shape: Any, z: float) -> list[list[tuple[float, float, float]]]:
        return self._planner._section_brep_at_projection(shape, z)

    @staticmethod
    def _compute_region_center(loops: list[list[np.ndarray]]) -> np.ndarray:
        all_p = [p for loop in loops for p in loop]
        return np.mean(all_p, axis=0) if all_p else np.zeros(3)

    @staticmethod
    def _compute_loop_span(loop: list[np.ndarray], basis: np.ndarray) -> float:
        vals = [float(np.dot(p, basis)) for p in loop]
        return max(vals) - min(vals) if vals else 0.0

    # ------------------------------------------------------------------
    # Fill strategy
    # ------------------------------------------------------------------

    def _fill_region(
        self, region: PlanarRegion, parameters: MachiningParameters,
    ) -> tuple[list[list[np.ndarray]], str]:
        contour_paths = self._planner._build_cavity_contour_parallel_paths(
            region.boundary_loops, parameters, allow_occ_offsets=False,
        )
        if contour_paths:
            log_msg = f"  contour-parallel: {len(contour_paths)} paths"
            logger.info(log_msg)
            return contour_paths, "contour_parallel"

        cut_lines = self._planner._build_cavity_cut_lines(region, parameters)
        if cut_lines:
            log_msg = f"  raster fallback: {len(cut_lines)} lines"
            logger.info(log_msg)
            return [[start, end] for start, end in cut_lines], "raster_bbox"

        logger.info("  fill vuoto (entrambe le strategie fallite)")
        return [], "empty"

    # ------------------------------------------------------------------
    # Motion generation per Z pass
    # ------------------------------------------------------------------

    def _build_pass_motions(
        self,
        cut_paths: list[list[np.ndarray]],
        pass_proj: float,
        parameters: MachiningParameters,
        prev_pass_proj: Optional[float],
    ) -> list[MotionCommand]:
        if not cut_paths:
            return []

        projected_paths: list[list[np.ndarray]] = []
        for path_idx, cut_path in enumerate(cut_paths):
            ordered = cut_path if path_idx % 2 == 0 else list(reversed(cut_path))
            projected = [self._planner._point_at_projection(p, pass_proj) for p in ordered]
            projected_paths.append(projected)

        motions: list[MotionCommand] = []

        first_path = projected_paths[0]
        motions.extend(self._build_approach(first_path, pass_proj, parameters, prev_pass_proj))

        for point in first_path:
            motions.append(MotionCommand(
                move="linear", point=self._pt(point), feed=parameters.cut_feed,
            ))

        for path_idx in range(1, len(projected_paths)):
            prev_end = projected_paths[path_idx - 1][-1]
            curr_start = projected_paths[path_idx][0]
            gap = float(np.linalg.norm(curr_start - prev_end))

            if gap > self._tool_diameter * 3.0:
                safe_pt = self._planner._point_at_projection(prev_end, parameters.cut_feed * 0.3)
                motions.append(MotionCommand(move="rapid", point=self._pt(
                    self._planner._point_at_projection(prev_end, parameters.cut_feed * 0.3))))
                motions.append(MotionCommand(move="rapid", point=self._pt(curr_start)))
                motions.append(MotionCommand(
                    move="linear", point=self._pt(curr_start), feed=parameters.plunge_feed,
                ))
            else:
                motions.append(MotionCommand(
                    move="linear", point=self._pt(curr_start), feed=parameters.cut_feed,
                ))

            for point in projected_paths[path_idx]:
                motions.append(MotionCommand(
                    move="linear", point=self._pt(point), feed=parameters.cut_feed,
                ))

        last_pt = projected_paths[-1][-1]
        motions.append(MotionCommand(move="rapid", point=self._pt(
            self._planner._point_at_projection(last_pt, parameters.cut_feed * 0.3))))
        return motions

    def _build_approach(
        self,
        first_path: list[np.ndarray],
        pass_proj: float,
        parameters: MachiningParameters,
        prev_pass_proj: Optional[float],
    ) -> list[MotionCommand]:
        start_pt = first_path[0]
        motions: list[MotionCommand] = []
        safe_z = pass_proj + parameters.cut_feed * 0.3  # not used as Z, just a marker

        if prev_pass_proj is None:
            safe_pt = self._planner._point_at_projection(start_pt, safe_z)
            motions.append(MotionCommand(move="rapid", point=self._pt(safe_pt)))
            motions.append(MotionCommand(
                move="linear", point=self._pt(start_pt), feed=parameters.plunge_feed,
            ))
        else:
            ramp_points = self._build_helical_ramp(
                center=start_pt,
                start_z=prev_pass_proj + self._clearance_z,
                end_z=pass_proj,
                radius=self._tool_radius * 0.8,
                turns=self._helical_turns,
            )
            entry = ramp_points[0] if ramp_points else start_pt
            motions.append(MotionCommand(move="rapid", point=self._pt(entry)))
            for rp in ramp_points[1:]:
                motions.append(MotionCommand(
                    move="linear", point=self._pt(rp), feed=parameters.plunge_feed,
                ))
            if np.linalg.norm(np.array(ramp_points[-1]) - start_pt) > 1e-6:
                motions.append(MotionCommand(
                    move="linear", point=self._pt(start_pt), feed=parameters.cut_feed,
                ))
        return motions

    # ------------------------------------------------------------------
    # Helical ramp
    # ------------------------------------------------------------------

    def _build_helical_ramp(
        self,
        center: np.ndarray,
        start_z: float,
        end_z: float,
        radius: float,
        turns: int = 2,
    ) -> list[np.ndarray]:
        if abs(end_z - start_z) < 1e-6:
            return [np.array(center)]
        total_angle = float(turns) * 2.0 * math.pi
        segments = max(16, int(turns * 16))
        points: list[np.ndarray] = []
        for i in range(segments + 1):
            frac = float(i) / float(segments)
            z = float(start_z) + (float(end_z) - float(start_z)) * frac
            x = float(center[0]) + float(radius) * math.cos(total_angle * frac)
            y = float(center[1]) + float(radius) * math.sin(total_angle * frac)
            points.append(np.array([x, y, z], dtype=float))
        return points

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _pt(point: np.ndarray) -> tuple[float, float, float]:
        return (float(point[0]), float(point[1]), float(point[2]))
