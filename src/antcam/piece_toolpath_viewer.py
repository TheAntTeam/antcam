from __future__ import annotations

import sys
from typing import Any, Optional

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from antcam.viewer import AntCamViewerWindow


class PieceToolpathPreviewWindow(AntCamViewerWindow):
    """Preview window showing piece, offset surface contours, and profile paths."""

    _OFFSET_CONTOUR_COLOR = (1.0, 0.55, 0.0)
    _OFFSET_CONTOUR_WIDTH = 3.0
    _DEBUG_OUTER_COLOR = (0.2, 0.4, 1.0)
    _DEBUG_HOLE_COLOR = (1.0, 0.2, 0.2)
    _DEBUG_WIRE_WIDTH = 1.0
    _CONTOUR_CUT_COLOR = (1.0, 0.9, 0.1)
    _CONTOUR_RAPID_COLOR = (0.1, 0.8, 1.0)
    _CONTOUR_CUT_WIDTH = 3.0
    _CONTOUR_RAPID_WIDTH = 1.8

    def __init__(
        self,
        model,
        features,
        working_plane_normal=(0.0, 0.0, 1.0),
        contour_shadow=None,
        perimeter=None,
        vertical_arc_groups=None,
        toolpath_plan=None,
        diagnostics: Optional[dict[str, Any]] = None,
        planner_config: Optional[Any] = None,
        roughing_offset_body=None,
        offset_distance: float = 1.0,
    ):
        self._preview_diagnostics = diagnostics or {}
        self._preview_planner_config = planner_config
        self._roughing_offset_body = roughing_offset_body
        self._offset_distance = offset_distance
        self._offset_contour_ais: list[Any] = []
        self._offset_debug_ais: list[Any] = []
        self._roughing_offset_ais = None
        self._roughing_offset_wire_ais = None
        super().__init__(
            model,
            features,
            working_plane_normal=working_plane_normal,
            contour_shadow=contour_shadow,
            perimeter=perimeter,
            vertical_arc_groups=vertical_arc_groups,
            toolpath_plan=toolpath_plan,
            enable_face_selection=False,
            show_toolpath_legend=False,
            show_model_edges=True,
            status_message="Piece preview | sezioni Z-level: blu=esterno rosso=interno",
        )
        self._toolpath_mode_cycle = ("visible", "hidden")
        self._toolpath_mode_filter = "hidden"
        self._offset_distance = offset_distance

        self.setWindowTitle("AntCAM Piece Toolpath Preview")
        self.resize(1400, 900)
        QTimer.singleShot(800, self._display_roughing_offset)

    # ------------------------------------------------------------------
    # Parametric offset control
    # ------------------------------------------------------------------

    def _create_offset_control(self):
        dock = QDockWidget("Offset", self)
        dock.setFeatures(QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetMovable)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(QLabel("Distanza offset:"))
        spin = QDoubleSpinBox()
        spin.setRange(0.1, 50.0)
        spin.setSingleStep(0.5)
        spin.setDecimals(1)
        spin.setValue(self._offset_distance)
        spin.valueChanged.connect(self._on_offset_distance_changed)
        layout.addWidget(spin)
        layout.addStretch()
        dock.setWidget(container)
    # ------------------------------------------------------------------
    # Offset display + per-Z offset contours
    # ------------------------------------------------------------------

    def _display_roughing_offset(self):
        if not self.ocp_widget._is_initialized:
            QTimer.singleShot(200, self._display_roughing_offset)
            return
        try:
            # 1) Show 3D offset body if available (orange transparent)
            self._display_3d_offset_body()
            # 2) Compute and show per-Z 2D offset contours (always)
            QTimer.singleShot(100, self._display_per_z_offset_contours)
        except Exception as exc:
            self.statusBar().showMessage(f"ERRORE offset: {exc}")

    def _display_3d_offset_body(self):
        shape = self._roughing_offset_body
        if shape is None:
            self.statusBar().showMessage("Offset 3D: nessun offset calcolato")
            return

        if self._body_ais is not None:
            try:
                self._body_ais.SetTransparency(0.3)
                self.ocp_widget.context.Redisplay(self._body_ais, True)
            except Exception:
                pass

        if self._roughing_offset_ais is not None:
            try:
                self.ocp_widget.context.Remove(self._roughing_offset_ais, True)
            except Exception:
                pass
            self._roughing_offset_ais = None

        self._roughing_offset_ais = self.ocp_widget.display_shape(
            shape,
            color=(1.0, 0.55, 0.0),
            transparency=0.45,
            selectable=False,
        )
        try:
            self._roughing_offset_wire_ais = self.ocp_widget.display_wire(
                shape,
                color=(1.0, 0.65, 0.0),
                width=2.5,
                selectable=False,
            )
        except Exception:
            self._roughing_offset_wire_ais = None

        try:
            self.ocp_widget.view.FitAll()
            self.ocp_widget.view.Redraw()
        except Exception:
            pass

    def _display_per_z_offset_contours(self):
        """Classify perimeters of extracted faces: outer=rosso, holes=blu.

        Per ogni Z-level ricostruisce la faccia (outer + holes), poi
        estrae i wire dalla faccia e li colora per ruolo:
          - rosso  (spessore 2.5) → perimetro esterno
          - blu    (spessore 1.5) → perimetri interni (fori/isole)
        """
        planner_config = self._preview_planner_config
        diagnostics = self._preview_diagnostics
        if planner_config is None or diagnostics is None:
            return

        model_brep = getattr(self.model, "brep", None)
        if model_brep is None:
            self.statusBar().showMessage("Face perimeters: nessun BRep disponibile")
            return

        proj_bounds = diagnostics.get("piece_projection_bounds")
        if proj_bounds is None:
            return

        top = float(proj_bounds["top"])
        bottom = float(proj_bounds["bottom"])
        depth = top - bottom
        if depth <= 1e-6:
            return

        w = (float(self.working_plane_normal[0]),
             float(self.working_plane_normal[1]),
             float(self.working_plane_normal[2]))
        planner = planner_config.build_planner(w)

        max_stepdown = float(getattr(planner_config, "max_stepdown", 2.0))
        z_levels = planner._build_depth_pass_projections(top, bottom, max_stepdown=max_stepdown)
        if not z_levels:
            self.statusBar().showMessage("Face perimeters: nessun Z-level generato")
            return

        # Clear old overlays
        for ais in self._offset_contour_ais:
            try:
                self.ocp_widget.context.Remove(ais, True)
            except Exception:
                pass
        self._offset_contour_ais.clear()
        for ais in self._offset_debug_ais:
            try:
                self.ocp_widget.context.Remove(ais, True)
            except Exception:
                pass
        self._offset_debug_ais.clear()

        from antcam.slicer import (
            _brep_section_get_wires,
            _brep_section_classify_wires,
            _brep_wire_to_polyline,
            _point_in_2d_polygon,
            _wires_to_face_with_holes,
        )
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
        from OCP.GeomAbs import GeomAbs_Intersection
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_WIRE
        from OCP.TopoDS import TopoDS

        off_dist = self._offset_distance

        def _offset_occ(wire, dist):
            try:
                ob = BRepOffsetAPI_MakeOffset()
                ob.Init(GeomAbs_Intersection)
                ob.AddWire(wire)
                ob.Perform(float(dist), 0.0)
                s = ob.Shape()
                if s.IsNull():
                    return []
                ws = []
                ew = TopExp_Explorer(s, TopAbs_WIRE)
                while ew.More():
                    ws.append(TopoDS.Wire_s(ew.Current()))
                    ew.Next()
                return ws
            except Exception:
                return []

        off_dist = self._offset_distance
        n_outer = 0
        n_holes = 0
        n_off = 0

        for z in z_levels:
            try:
                wires = _brep_section_get_wires(model_brep, z, w)
            except Exception:
                continue
            if not wires:
                continue

            groups = _brep_section_classify_wires(wires)
            if not groups:
                continue

            for outer_wire, hole_wires in groups:
                try:
                    face = _wires_to_face_with_holes(outer_wire, hole_wires)
                except Exception:
                    continue

                # Costruisce polilinee per point-in-face test
                try:
                    face_outer_pts = _brep_wire_to_polyline(outer_wire)
                    face_hole_pts = [_brep_wire_to_polyline(h) for h in hole_wires]
                except Exception:
                    continue

                def _sample_inside_face(pts, exclude_idx=None):
                    """True se almeno un punto dei pts e' dentro la faccia (material).
                    exclude_idx: indice dell'hole da escludere (il foro stesso).
                    """
                    for p in pts:
                        if not _point_in_2d_polygon((p[0], p[1]), face_outer_pts):
                            continue
                        in_hole = any(
                            _point_in_2d_polygon((p[0], p[1]), hp)
                            for hi, hp in enumerate(face_hole_pts)
                            if hi != exclude_idx
                        )
                        if not in_hole:
                            return True
                    return False

                def _offset_face(wire, dist, exclude_idx=None):
                    offset = _offset_occ(wire, dist)
                    if not offset:
                        return []
                    for ow in offset:
                        ow_pts = _brep_wire_to_polyline(ow)
                        if not _sample_inside_face(ow_pts, exclude_idx=exclude_idx):
                            offset2 = _offset_occ(wire, -dist)
                            return offset2 if offset2 else offset
                    return offset

                # Originale outer (rosso, sottile)
                try:
                    ais = self.ocp_widget.display_wire(
                        outer_wire, color=(1.0, 0.15, 0.15), width=1.0, selectable=False,
                    )
                    self._offset_debug_ais.append(ais)
                    n_outer += 1
                except Exception:
                    pass

                # Offset outer (arancio, spesso)
                for ow in _offset_face(outer_wire, off_dist):
                    try:
                        ais = self.ocp_widget.display_wire(
                            ow, color=(1.0, 0.6, 0.0), width=2.5, selectable=False,
                        )
                        self._offset_debug_ais.append(ais)
                        n_off += 1
                    except Exception:
                        pass

                for hi, hw in enumerate(hole_wires):
                    # Originale hole (blu, sottile)
                    try:
                        ais = self.ocp_widget.display_wire(
                            hw, color=(0.15, 0.4, 1.0), width=1.0, selectable=False,
                        )
                        self._offset_debug_ais.append(ais)
                        n_holes += 1
                    except Exception:
                        pass

                    # Offset hole (ciano, spesso) — esclude se stesso dal test
                    for oh in _offset_face(hw, off_dist, exclude_idx=hi):
                        try:
                            ais = self.ocp_widget.display_wire(
                                oh, color=(0.0, 0.8, 0.9), width=2.0, selectable=False,
                            )
                            self._offset_debug_ais.append(ais)
                            n_off += 1
                        except Exception:
                            pass

        try:
            self.ocp_widget.view.FitAll()
            self.ocp_widget.view.Redraw()
        except Exception:
            pass

        if n_outer > 0 or n_holes > 0:
            self.statusBar().showMessage(
                f"Face perimeters: {len(z_levels)} Z-level, "
                f"{n_outer} outer, {n_holes} holes, {n_off} offset "
                f"(dist={off_dist:.1f})."
            )
        else:
            self.statusBar().showMessage("Face perimeters: nessun perimetro.")

    def _polyline_to_wire(self, poly: list[tuple[float, float, float]]) -> Any:
        """Convert a list of 3D points to an OCC wire (closed)."""
        from OCP.gp import gp_Pnt
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon

        if len(poly) < 3:
            return None
        builder = BRepBuilderAPI_MakePolygon()
        for p in poly:
            builder.Add(gp_Pnt(float(p[0]), float(p[1]), float(p[2])))
        try:
            builder.Close()
            return builder.Wire()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Toolpath display (solo scontornatura)
    # ------------------------------------------------------------------

    def _should_display_toolpath_operation(self, operation) -> bool:
        if self._toolpath_mode_filter == "hidden":
            return False
        strategy = str(getattr(operation, "strategy", "") or "").lower()
        operation_mode = str(getattr(operation, "metadata", {}).get("operation_mode", "") or "").lower()
        if strategy == "2p5d_profile":
            return True
        return strategy == "cavity_clearing" and operation_mode == "roughing"

    def _toolpath_filter_label(self) -> str:
        return "visibili" if self._toolpath_mode_filter == "visible" else "nascosti"

    def _cycle_toolpath_filter(self, step: int):
        operations = [
            op
            for op in getattr(self._toolpath_plan, "operations", [])
            if self._should_display_toolpath_operation(op)
        ] if self._toolpath_plan is not None else []
        if not operations:
            self.statusBar().showMessage("Piece preview | nessun path disponibile")
            return

        current_index = self._toolpath_mode_cycle.index(self._toolpath_mode_filter)
        direction = 1 if step >= 0 else -1
        self._toolpath_mode_filter = self._toolpath_mode_cycle[
            (current_index + direction) % len(self._toolpath_mode_cycle)
        ]
        self._display_toolpath_plan()

    def _display_toolpath_plan(self):
        if self._toolpath_plan is None:
            return

        for overlay in self._toolpath_ais:
            self.ocp_widget.remove_interactive(overlay)
        self._toolpath_ais.clear()

        if self._toolpath_mode_filter == "hidden":
            self.statusBar().showMessage("Piece preview | path nascosti | T mostra")
            return

        displayed_roughing_path_count = 0
        displayed_contour_path_count = 0
        for operation in getattr(self._toolpath_plan, "operations", []):
            if not self._should_display_toolpath_operation(operation):
                continue
            strategy = str(getattr(operation, "strategy", "") or "").lower()
            if strategy == "cavity_clearing":
                roughing_shapes = self._build_operation_display_shapes(operation)
                displayed_roughing_path_count += len(roughing_shapes)
                for shape in roughing_shapes:
                    overlay = self.ocp_widget.display_wire(
                        shape,
                        color=self._OFFSET_CONTOUR_COLOR,
                        width=self._OFFSET_CONTOUR_WIDTH,
                        selectable=False,
                    )
                    self._toolpath_ais.append(overlay)
                continue

            preview_shapes = self._build_profile_preview_shapes(operation)
            displayed_contour_path_count += len(preview_shapes["cut"])
            for shape in preview_shapes["rapid"]:
                overlay = self.ocp_widget.display_wire(
                    shape,
                    color=self._CONTOUR_RAPID_COLOR,
                    width=self._CONTOUR_RAPID_WIDTH,
                    selectable=False,
                )
                self._toolpath_ais.append(overlay)
            for shape in preview_shapes["cut"]:
                overlay = self.ocp_widget.display_wire(
                    shape,
                    color=self._CONTOUR_CUT_COLOR,
                    width=self._CONTOUR_CUT_WIDTH,
                    selectable=False,
                )
                self._toolpath_ais.append(overlay)

        if displayed_roughing_path_count or displayed_contour_path_count:
            self.statusBar().showMessage(
                "Piece preview | "
                f"sgrossatura: {displayed_roughing_path_count} path | "
                f"scontornatura: {displayed_contour_path_count} path | "
                "sgrossatura arancione, taglio giallo, rapido azzurro | T nasconde"
            )
        else:
            self.statusBar().showMessage("Piece preview | nessun path disponibile")

    def _build_profile_preview_shapes(self, operation) -> dict[str, list[Any]]:
        metadata = getattr(operation, "metadata", {}) or {}
        cut_feed = metadata.get("cut_feed")
        profile_motions = list(getattr(operation, "motions", []))
        cut_paths = self._split_toolpath_motion_paths(profile_motions, cut_feed=cut_feed)
        rapid_paths = self._split_rapid_motion_paths(profile_motions)
        return {
            "cut": [
                wire
                for path_points in cut_paths
                for wire in [self._make_polyline_wire(path_points)]
                if wire is not None
            ],
            "rapid": [
                wire
                for path_points in rapid_paths
                for wire in [self._make_polyline_wire(path_points)]
                if wire is not None
            ],
        }

    def _split_rapid_motion_paths(self, motions):
        paths = []
        current_path = []
        previous_point = None

        for motion in motions:
            point = np.array(getattr(motion, "point", ()), dtype=float)
            if point.shape != (3,):
                continue

            move = getattr(motion, "move", "")
            if move != "rapid":
                if len(current_path) >= 2:
                    paths.append(current_path)
                current_path = []
                previous_point = point
                continue

            if previous_point is None:
                previous_point = point
                continue

            if not current_path:
                current_path = [np.array(previous_point, dtype=float), point]
            elif np.linalg.norm(current_path[-1] - point) > 1e-7:
                current_path.append(point)
            previous_point = point

        if len(current_path) >= 2:
            paths.append(current_path)
        return paths


def show_piece_toolpath_preview(
    model,
    features,
    working_plane_normal=(0.0, 0.0, 1.0),
    contour_shadow=None,
    perimeter=None,
    vertical_arc_groups=None,
    toolpath_plan=None,
    diagnostics: Optional[dict[str, Any]] = None,
    planner_config: Optional[Any] = None,
    roughing_offset_body=None,
    offset_distance: float = 1.0,
):
    app = QApplication.instance() or QApplication(sys.argv)
    window = PieceToolpathPreviewWindow(
        model,
        features,
        working_plane_normal=working_plane_normal,
        contour_shadow=contour_shadow,
        perimeter=perimeter,
        vertical_arc_groups=vertical_arc_groups,
        toolpath_plan=toolpath_plan,
        diagnostics=diagnostics,
        planner_config=planner_config,
        roughing_offset_body=roughing_offset_body,
        offset_distance=offset_distance,
    )
    window.show()
    sys.exit(app.exec())
