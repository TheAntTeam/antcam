import sys
import logging
import ctypes
import math
from typing import Dict, List, Optional

from PySide6.QtWidgets import QMainWindow, QWidget, QApplication, QMenu, QLabel
from PySide6.QtCore import Qt, QTimer, QPoint, Signal

from OCP.AIS import AIS_InteractiveContext, AIS_Shape, AIS_Trihedron
from OCP.V3d import V3d_Viewer
from OCP.Aspect import Aspect_DisplayConnection
from OCP.OpenGl import OpenGl_GraphicDriver
from OCP.WNT import WNT_Window
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.TopoDS import TopoDS_Shape, TopoDS_Wire
from OCP.Geom import Geom_Axis2Placement
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_WIRE, TopAbs_EDGE, TopAbs_FACE
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
from OCP.Prs3d import Prs3d_LineAspect
from OCP.Aspect import Aspect_TOL_SOLID
import numpy as np

from antcam.face_selection import FaceSelectionService

logger = logging.getLogger("antcam")

def get_capsule_from_int(handle: int):
    ctypes.pythonapi.PyCapsule_New.restype = ctypes.py_object
    ctypes.pythonapi.PyCapsule_New.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
    return ctypes.pythonapi.PyCapsule_New(handle, None, None)

class QOCPWidget(QWidget):
    """Qt widget hosting an OCC 3D view and emitting high-level interaction signals.

    Rendering is throttled with a dirty-flag timer to reduce redundant redraws
    during resize and interactive camera manipulation.
    """
    face_clicked = Signal(object, bool)
    context_menu_requested = Signal(object, object)
    cycle_requested = Signal(int, object)
    clear_selection_requested = Signal()
    deselect_all_faces_requested = Signal()
    clear_all_requested = Signal()
    toggle_accessible_filter_requested = Signal()
    cycle_toolpath_filter_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setAttribute(Qt.WA_PaintOnScreen)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self._is_initialized = False
        self.context = None
        self.view = None
        self._last_pos = QPoint()
        self._left_press_pos = QPoint()
        self._left_dragging = False
        self._selection_enabled = False
        self._selection_target = None

        # Dirty flag per evitare redraw simultanei durante resize
        self._needs_redraw = False

        # Timer per il redraw continuo durante drag/resize
        self._redraw_timer = QTimer()
        self._redraw_timer.timeout.connect(self._periodic_redraw)
        self._redraw_timer.start(30)  # ~33 FPS per ridurre lo sfarfallio

    def paintEngine(self):
        return None

    def _init_ocp(self):
        if self._is_initialized: return
        try:
            display_connection = Aspect_DisplayConnection()
            graphic_driver = OpenGl_GraphicDriver(display_connection)
            viewer = V3d_Viewer(graphic_driver)
            viewer.SetDefaultLights()
            viewer.SetLightOn()
            self.view = viewer.CreateView()
            self.context = AIS_InteractiveContext(viewer)

            handle = int(self.winId())
            window_handle = WNT_Window(get_capsule_from_int(handle))
            self.view.SetWindow(window_handle)
            if not window_handle.IsMapped(): window_handle.Map()

            self.view.SetBackgroundColor(Quantity_Color(0.1, 0.1, 0.1, Quantity_TOC_RGB))
            self.view.MustBeResized()
            
            # AGGIUNTA TRIADE ASSI
            self._add_trihedron()
            
            self._is_initialized = True
        except Exception as e:
            logger.error(f"Errore inizializzazione OCP: {e}")

    def _add_trihedron(self):
        """Aggiunge un riferimento vettoriale (X=R, Y=G, Z=B)."""
        axis_placement = Geom_Axis2Placement(gp_Ax2(gp_Pnt(0,0,0), gp_Dir(0,0,1), gp_Dir(1,0,0)))
        trihedron = AIS_Trihedron(axis_placement)
        self.context.Display(trihedron, True)

    def _periodic_redraw(self):
        """Redraw periodico solo se marked come dirty (needs_redraw)."""
        if not self._is_initialized or not self.view:
            return

        if self._needs_redraw:
            self._needs_redraw = False
            try:
                self.view.Redraw()
            except Exception:
                pass

    def enable_face_selection(self, ais_shape):
        """Enable face-level picking on the displayed model AIS shape."""
        if not self._is_initialized or not self.context or ais_shape is None:
            return
        self._selection_target = ais_shape
        self._selection_enabled = True
        try:
            self.context.Deactivate(ais_shape)
        except Exception:
            pass
        try:
            self.context.SetSelectionModeActive(ais_shape, int(TopAbs_FACE), True)
        except Exception as e:
            logger.warning(f"Impossibile attivare la selezione facce: {e}")

    def detect_face_at(self, pos: QPoint):
        """Return the currently detected face under screen position, if any."""
        if not self._is_initialized or not self.context or not self.view or not self._selection_enabled:
            return None
        try:
            # Detection senza redraw forzato per evitare flicker durante interazioni/menu.
            self.context.MoveTo(pos.x(), pos.y(), self.view, False)
            if self.context.HasDetectedShape():
                return self.context.DetectedShape()
        except Exception as e:
            logger.debug(f"detect_face_at error: {e}")
        return None

    def get_detected_faces(self, pos: Optional[QPoint] = None, max_count: int = 64):
        """Collect faces under cursor ordered by OCC detection depth."""
        if not self._is_initialized or not self.context or not self.view or not self._selection_enabled:
            return []
        query_pos = pos or self._last_pos
        faces = []
        try:
            self.context.MoveTo(query_pos.x(), query_pos.y(), self.view, False)
            self.context.InitDetected()
            count = 0
            while self.context.MoreDetected() and count < max_count:
                shp = self.context.DetectedShape()
                if shp is not None:
                    is_dup = False
                    for existing in faces:
                        try:
                            if existing.IsSame(shp):
                                is_dup = True
                                break
                        except Exception:
                            continue
                    if not is_dup:
                        faces.append(shp)
                self.context.NextDetected()
                count += 1
        except Exception as e:
            logger.debug(f"get_detected_faces error: {e}")
        return faces

    def remove_interactive(self, ais_shape):
        if not self._is_initialized or not self.context or ais_shape is None:
            return
        try:
            self.context.Remove(ais_shape, False)
            self._needs_redraw = True
        except Exception:
            pass

    def mousePressEvent(self, event):
        self._last_pos = event.position().toPoint()
        self._left_press_pos = self._last_pos
        self._left_dragging = False
        if event.button() == Qt.LeftButton:
            if self._is_initialized:
                self.view.StartRotation(self._last_pos.x(), self._last_pos.y())

    def mouseMoveEvent(self, event):
        if not self._is_initialized: return
        pos = event.position().toPoint()
        if event.buttons() & Qt.LeftButton:
            if (pos - self._left_press_pos).manhattanLength() > 3:
                self._left_dragging = True
            self.view.Rotation(pos.x(), pos.y())
            self._needs_redraw = True
        elif event.buttons() & Qt.MiddleButton:
            self.view.Pan(pos.x() - self._last_pos.x(), self._last_pos.y() - pos.y())
            self._needs_redraw = True
        self._last_pos = pos

    def mouseReleaseEvent(self, event):
        pos = event.position().toPoint()
        if event.button() == Qt.LeftButton and not self._left_dragging:
            face = self.detect_face_at(pos)
            ctrl_pressed = bool(event.modifiers() & Qt.ControlModifier)
            self.face_clicked.emit(face, ctrl_pressed)
        elif event.button() == Qt.RightButton:
            face = self.detect_face_at(pos)
            self.context_menu_requested.emit(face, event.globalPosition().toPoint())
        self._left_dragging = False
        self._last_pos = pos

    def wheelEvent(self, event):
        if not self._is_initialized: return
        if self._selection_enabled and (event.modifiers() & Qt.ShiftModifier):
            step = 1 if event.angleDelta().y() > 0 else -1
            pos = event.position().toPoint()
            self._last_pos = pos
            self.cycle_requested.emit(step, pos)
            event.accept()
            return
        zoom_factor = 1.1 if event.angleDelta().y() > 0 else 0.9
        self.view.SetZoom(zoom_factor)
        self._needs_redraw = True

    def paintEvent(self, event):
        if not self._is_initialized: 
            self._init_ocp()
        self._needs_redraw = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._is_initialized and self.view:
            self.view.MustBeResized()
            self._needs_redraw = True

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Tab:
            self.cycle_requested.emit(-1 if (event.modifiers() & Qt.ShiftModifier) else 1)
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.clear_selection_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_A:
            self.toggle_accessible_filter_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_D:
            self.deselect_all_faces_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_T:
            self.cycle_toolpath_filter_requested.emit(-1 if (event.modifiers() & Qt.ShiftModifier) else 1)
            event.accept()
            return
        if event.key() == Qt.Key_R and (event.modifiers() & Qt.ControlModifier):
            self.clear_all_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def display_shape(self, shape: TopoDS_Shape, color=(0.7, 0.7, 0.7), transparency=0.0, selectable=False):
        if not self._is_initialized: self._init_ocp()
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        BRepMesh_IncrementalMesh(shape, 0.1)
        ais_shape = AIS_Shape(shape)
        q_color = Quantity_Color(color[0], color[1], color[2], Quantity_TOC_RGB)
        ais_shape.SetColor(q_color)
        ais_shape.SetDisplayMode(1)
        if transparency > 0: ais_shape.SetTransparency(transparency)
        from OCP.Graphic3d import Graphic3d_MaterialAspect, Graphic3d_NOM_PLASTIC
        mat = Graphic3d_MaterialAspect(Graphic3d_NOM_PLASTIC)
        ais_shape.SetMaterial(mat)
        self.context.Display(ais_shape, True)
        if not selectable:
            self.context.Deactivate(ais_shape)
        self._needs_redraw = True
        return ais_shape

    def display_shadow(self, shape: TopoDS_Shape, color=(1.0, 0.5, 0.0), transparency=0.3, selectable=False):
        """Visualizza uno shape in modalita' shaded semitrasparente."""
        if not self._is_initialized: self._init_ocp()
        from OCP.Graphic3d import Graphic3d_MaterialAspect, Graphic3d_NOM_PLASTIC
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        BRepMesh_IncrementalMesh(shape, 0.1)
        ais_shape = AIS_Shape(shape)
        q_color = Quantity_Color(color[0], color[1], color[2], Quantity_TOC_RGB)
        ais_shape.SetColor(q_color)
        mat = Graphic3d_MaterialAspect(Graphic3d_NOM_PLASTIC)
        ais_shape.SetMaterial(mat)
        ais_shape.SetDisplayMode(1)
        if transparency > 0.0:
            ais_shape.SetTransparency(transparency)
        self.context.Display(ais_shape, True)
        if not selectable:
            self.context.Deactivate(ais_shape)
        self._needs_redraw = True
        return ais_shape

    def display_wire(self, shape: TopoDS_Shape, color=(1.0, 1.0, 0.0), width=3.0, selectable=False):
        """Visualizza tutti i wire/edge di uno shape con colore e spessore."""
        if not self._is_initialized: self._init_ocp()
        ais_shape = AIS_Shape(shape)
        q_color = Quantity_Color(color[0], color[1], color[2], Quantity_TOC_RGB)
        ais_shape.SetColor(q_color)
        ais_shape.SetDisplayMode(0)
        ais_shape.SetWidth(width)
        self.context.Display(ais_shape, True)
        if not selectable:
            self.context.Deactivate(ais_shape)
        self._needs_redraw = True
        return ais_shape

class AntCamViewerWindow(QMainWindow):
    """Main interactive window used to inspect and manually tag STEP features."""
    def __init__(
        self,
        model,
        features: List,
        working_plane_normal=(0.0, 0.0, 1.0),
        contour_shadow=None,
        perimeter=None,
        vertical_arc_groups=None,
        toolpath_plan=None,
        *,
        enable_face_selection: bool = True,
        show_toolpath_legend: bool = True,
        show_model_edges: bool = False,
        model_edge_color=(0.18, 0.18, 0.18),
        model_edge_width: float = 1.5,
        status_message: Optional[str] = None,
    ):
        super().__init__()
        self.setWindowTitle("AntCAM Interactive Viewer")
        self.resize(1200, 850)
        self.ocp_widget = QOCPWidget(self)
        self.setCentralWidget(self.ocp_widget)
        self.model = model
        self.features = features
        self.working_plane_normal = np.array(working_plane_normal)
        self._contour_shadow = contour_shadow
        self._perimeter = perimeter or []
        self._vertical_arc_groups = vertical_arc_groups or []
        self._toolpath_plan = toolpath_plan
        self._enable_face_selection = bool(enable_face_selection)
        self._show_toolpath_legend = bool(show_toolpath_legend)
        self._show_model_edges = bool(show_model_edges)
        self._model_edge_color = tuple(float(v) for v in model_edge_color)
        self._model_edge_width = float(model_edge_width)
        self._toolpath_mode_cycle = ("all", "drilling", "roughing", "finishing")
        self._toolpath_mode_filter = "all"
        self._toolpath_legend_label = QLabel(self._toolpath_legend_html())
        self._default_status_message = status_message or (
            "Viewer pronto | Esc deseleziona corrente | D deseleziona tutte le facce | T cicla toolpath | Ctrl+R reset manuali"
        )
        self._selection_service: Optional[FaceSelectionService] = None
        self._accessible_only = True
        self._body_ais = None
        self._body_edge_ais = None
        self._toolpath_ais: List[object] = []
        self._selected_face_id: Optional[int] = None
        self._selected_face_ais = None
        self._selected_face_ids = set()
        self._selected_face_overlays: Dict[int, object] = {}
        self._manual_face_assignments: Dict[int, str] = {}
        self._manual_face_ais: Dict[int, Dict[str, object]] = {}
        self.ocp_widget.face_clicked.connect(self._on_face_clicked)
        self.ocp_widget.context_menu_requested.connect(self._on_context_menu_requested)
        self.ocp_widget.cycle_requested.connect(self._on_cycle_requested)
        self.ocp_widget.clear_selection_requested.connect(self._clear_face_selection)
        self.ocp_widget.deselect_all_faces_requested.connect(self._deselect_all_faces)
        self.ocp_widget.clear_all_requested.connect(self._clear_all_manual_assignments)
        self.ocp_widget.toggle_accessible_filter_requested.connect(self._toggle_accessible_filter)
        self.ocp_widget.cycle_toolpath_filter_requested.connect(self._cycle_toolpath_filter)
        if self._show_toolpath_legend:
            self.statusBar().addPermanentWidget(self._toolpath_legend_label, 1)
        self.statusBar().showMessage(self._default_status_message)
        QTimer.singleShot(500, self._load_data)

    def _load_data(self):
        """Load model geometry and initialize rendering overlays.

        The body is always displayed first, then extracted feature overlays are
        drawn. Face-selection services are only enabled for real BRep models.
        """
        if not self.model: return

        # Prefer native BRep; fallback to on-the-fly mesh tessellation shape.
        shape_to_display = self.model.brep

        # Se non c'è BRep ma c'è mesh (STL), converti la mesh in shape OCP
        if not shape_to_display and self.model.mesh:
            logger.info("Nessun BRep disponibile, converto mesh STL a OCP shape")
            shape_to_display = self._mesh_to_ocp_shape(self.model.mesh)

        if not shape_to_display: return

        bbox = Bnd_Box()
        BRepBndLib.Add_s(shape_to_display, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        cx, cy, cz = (xmin+xmax)/2, (ymin+ymax)/2, (zmin+zmax)/2
        
        self._body_ais = self.ocp_widget.display_shape(
            shape_to_display,
            transparency=0.0,
            selectable=bool(self.model and self.model.brep),
        )
        if self._show_model_edges:
            self._body_edge_ais = self.ocp_widget.display_wire(
                shape_to_display,
                color=self._model_edge_color,
                width=self._model_edge_width,
                selectable=False,
            )
        colors = {
            "hole_group": (1.0, 0.0, 0.0),       # rosso
            "countersunk_hole": (1.0, 0.3, 0.0),  # rosso-arancio
            "pocket": (0.0, 0.3, 1.0),            # blu
            "step": (0.0, 0.6, 1.0),              # blu chiaro
            "opening": (0.0, 1.0, 1.0),           # ciano
            "slot": (0.0, 1.0, 1.0),              # ciano
            "fillet": (0.0, 1.0, 0.0),            # verde
            "chamfer": (1.0, 1.0, 0.0),           # giallo
        }
        for feat in self.features:
            color = colors.get(feat.type, (1.0, 0.0, 1.0))
            if feat.type == "hole_group":
                is_through = feat.props.get("through", False)
                color = (1.0, 0.0, 1.0) if is_through else (1.0, 0.0, 0.0)
                show_hole_faces = False  # flag per mostrare facce cilindriche semitrasparenti
                for hole in feat.holes:
                    # Mostra sempre gli edge delle facce cilindriche (sia arc-groups che hole standard)
                    faces_to_show = hole.props.get("all_faces") or ([hole.geometry] if hole.geometry else [])
                    for f in faces_to_show:
                        self.ocp_widget.display_wire(f, color=color, width=1.0, selectable=False)
                    if show_hole_faces:
                        for f in faces_to_show:
                            self.ocp_widget.display_shadow(f, color=color, transparency=0.6, selectable=False)
                    # Mostra cap_faces semitrasparenti in verde chiaro
                    show_cap_faces = False  # flag per mostrare i candidati tappo
                    if show_cap_faces:
                        for cap in hole.props.get("cap_faces", []):
                            self.ocp_widget.display_shadow(cap, color=(0.2, 1.0, 0.2), transparency=0.4, selectable=False)
                continue
            if feat.geometry:
                if feat.props.get("through"):
                    wire = self._extract_zmin_wire(feat.geometry)
                    if wire:
                        self.ocp_widget.display_wire(wire, color=color, width=4.0, selectable=False)
                        continue
                self.ocp_widget.display_shape(feat.geometry, color=color, selectable=False)

        if self._enable_face_selection and self.model and self.model.brep and self._body_ais is not None:
            self._selection_service = FaceSelectionService(self.model, tuple(self.working_plane_normal))
            self._selection_service.build_candidates()
            self.ocp_widget.enable_face_selection(self._body_ais)
            self._update_filter_status()

        self.ocp_widget.view.SetProj(1, -1, 1)
        self.ocp_widget.view.FitAll()
        self.ocp_widget.view.SetAt(cx, cy, cz)
        self.ocp_widget.view.Redraw()

        # Visualizza ombra proiettata a z-min: arancio semitrasparente
        # (disabilitata, ripristinare show_shadow=True per visualizzarla)
        show_shadow = False
        if show_shadow and self._contour_shadow is not None:
            self.ocp_widget.display_shadow(self._contour_shadow)
        # Visualizza facce verticali con archi XY (debug fori sul bordo)
        # Disabilitato - usare show_arc_faces = True per riabilitare
        show_arc_faces = False
        if show_arc_faces:
            for i, group in enumerate(self._vertical_arc_groups):
                complete_z = group.get("complete_z", {})
                if len(complete_z) >= 2:
                    for face in group["faces"]:
                        self.ocp_widget.display_shadow(face, color=(0.0, 0.8, 1.0), transparency=0.5, selectable=False)
        # Visualizza perimetro ombra in arancio pieno
        for wire in self._perimeter:
            self.ocp_widget.display_wire(wire, color=(1.0, 0.5, 0.0), width=2.0, selectable=False)
        self._display_toolpath_plan()
        self.ocp_widget.view.Redraw()

    def _update_filter_status(self):
        if not self._selection_service:
            return
        visible = self._selection_service.get_visible_candidates(self._accessible_only)
        mode = "solo accessibili dall'alto" if self._accessible_only else "tutte le facce"
        toolpath_mode = self._toolpath_filter_label()
        self.statusBar().showMessage(
            f"Edit feature: {mode} | toolpath={toolpath_mode} | {len(visible)} facce candidate | click seleziona, Ctrl+click multi-selezione, Shift+rotella cicla sotto cursore, D deseleziona tutte"
        )

    def _clear_face_selection(self):
        """Clear active face selection overlays while keeping manual assignments."""
        self._selected_face_id = None
        if self._selected_face_ais is not None:
            self.ocp_widget.remove_interactive(self._selected_face_ais)
            self._selected_face_ais = None
        for overlay in self._selected_face_overlays.values():
            self.ocp_widget.remove_interactive(overlay)
        self._selected_face_overlays.clear()
        self._selected_face_ids.clear()
        self._update_filter_status()

    def _clear_all_manual_assignments(self):
        """Remove all user-assigned feature overlays and reset selection state."""
        self._manual_face_assignments.clear()
        for overlays in self._manual_face_ais.values():
            fill = overlays.get("fill")
            wire = overlays.get("wire")
            if fill is not None:
                self.ocp_widget.remove_interactive(fill)
            if wire is not None:
                self.ocp_widget.remove_interactive(wire)
        self._manual_face_ais.clear()
        self._clear_face_selection()
        self.statusBar().showMessage("Assegnazioni manuali rimosse")

    def _deselect_all_faces(self):
        """Rimuove solo lo stato di selezione corrente, mantenendo le assegnazioni manuali."""
        self._clear_face_selection()
        self.statusBar().showMessage("Tutte le facce deselezionate")

    def _toggle_accessible_filter(self):
        """Toggle candidate visibility between accessible-only and all faces."""
        if not self._selection_service:
            return
        self._accessible_only = not self._accessible_only
        if self._selected_face_id is not None:
            candidate = self._selection_service.get_candidate(self._selected_face_id)
            if candidate and not (candidate.accessible_from_top or not self._accessible_only):
                self._clear_face_selection()
        self._update_filter_status()

    def _cycle_toolpath_filter(self, step: int):
        """Cycle the visible toolpath subset between all, drilling, roughing, and finishing."""
        operations = list(getattr(self._toolpath_plan, "operations", [])) if self._toolpath_plan is not None else []
        if not operations:
            self.statusBar().showMessage("Nessun toolpath disponibile da filtrare")
            return

        current_index = self._toolpath_mode_cycle.index(self._toolpath_mode_filter)
        self._toolpath_mode_filter = self._toolpath_mode_cycle[(current_index + step) % len(self._toolpath_mode_cycle)]
        self._display_toolpath_plan()
        self.statusBar().showMessage(
            f"Visualizzazione toolpath: {self._toolpath_filter_label()} | T avanti | Shift+T indietro"
        )

    def _on_face_clicked(self, face_shape, ctrl_pressed: bool):
        """Handle left-click selection with optional Ctrl-based multi-selection."""
        if not self._selection_service:
            return
        candidate = self._selection_service.find_candidate_by_shape(face_shape, accessible_only=self._accessible_only)
        if candidate is None and not self._accessible_only:
            candidate = self._selection_service.find_candidate_by_shape(face_shape, accessible_only=False)
        if candidate is None:
            self._clear_face_selection()
            self.statusBar().showMessage("Nessuna faccia selezionata")
            return

        if ctrl_pressed:
            self._toggle_face_candidate(candidate)
        else:
            self._select_face_candidate(candidate)

    def _on_cycle_requested(self, step: int, mouse_pos):
        """Cycle selection through candidates detected below current cursor position."""
        if not self._selection_service:
            return
        candidate = self._cycle_candidate_under_cursor(step, mouse_pos)
        if candidate is not None:
            self._select_face_candidate(candidate)
        else:
            self.statusBar().showMessage("Nessuna faccia accessibile sotto il puntatore")

    def _cycle_candidate_under_cursor(self, step: int, mouse_pos=None):
        """Return next selectable candidate from the under-cursor detection stack.

        The cycle is intentionally local: only faces currently under the pointer
        are considered, then filtered by accessibility mode.
        """
        if not self._selection_service:
            return None
        detected_shapes = self.ocp_widget.get_detected_faces(pos=mouse_pos)
        if not detected_shapes:
            return None

        ordered_ids = []
        for shp in detected_shapes:
            candidate = self._selection_service.find_candidate_by_shape(shp, accessible_only=self._accessible_only)
            if candidate is None and not self._accessible_only:
                candidate = self._selection_service.find_candidate_by_shape(shp, accessible_only=False)
            if candidate is None:
                continue
            if candidate.face_id not in ordered_ids:
                ordered_ids.append(candidate.face_id)

        if not ordered_ids:
            return None

        if self._selected_face_id in ordered_ids:
            idx = ordered_ids.index(self._selected_face_id)
            next_idx = (idx + step) % len(ordered_ids)
        else:
            next_idx = 0 if step >= 0 else -1
        return self._selection_service.get_candidate(ordered_ids[next_idx])

    def _select_face_candidate(self, candidate):
        """Make one candidate active and render its selection overlay."""
        self._clear_face_selection()
        self._selected_face_id = candidate.face_id
        self._selected_face_ids.add(candidate.face_id)
        self._selected_face_ais = self.ocp_widget.display_shadow(
            candidate.face,
            color=(1.0, 1.0, 0.0),
            transparency=0.15,
            selectable=False,
        )
        self._selected_face_overlays[candidate.face_id] = self._selected_face_ais
        auto_types = self._auto_feature_types_for_face(candidate.face)
        manual_type = self._manual_face_assignments.get(candidate.face_id)
        manual_text = f" | manuale={manual_type}" if manual_type else ""
        auto_text = f" | auto={','.join(auto_types)}" if auto_types else ""
        self.statusBar().showMessage(
            f"Faccia #{candidate.face_id} [{candidate.surface_type}] accessibile={candidate.accessible_from_top}"
            f" | compatibili={','.join(candidate.compatible_features) or '-'}{auto_text}{manual_text}"
        )

    def _toggle_face_candidate(self, candidate):
        """Toggle one candidate in the multi-selection set (Ctrl+click semantics)."""
        fid = candidate.face_id
        if fid in self._selected_face_ids:
            self._selected_face_ids.remove(fid)
            overlay = self._selected_face_overlays.pop(fid, None)
            if overlay is not None:
                self.ocp_widget.remove_interactive(overlay)
            if self._selected_face_id == fid:
                self._selected_face_id = next(iter(self._selected_face_ids), None)
        else:
            self._selected_face_ids.add(fid)
            overlay = self.ocp_widget.display_shadow(
                candidate.face,
                color=(1.0, 1.0, 0.0),
                transparency=0.15,
                selectable=False,
            )
            self._selected_face_overlays[fid] = overlay
            self._selected_face_id = fid

        self._selected_face_ais = self._selected_face_overlays.get(self._selected_face_id)
        self.statusBar().showMessage(f"Facce selezionate: {len(self._selected_face_ids)}")

    def _on_context_menu_requested(self, face_shape, global_pos):
        """Open context menu bound to current selection without re-picking faces."""
        if not self._selection_service:
            return
        candidate = None
        # Mantiene la selezione attiva corrente: il click destro non cambia selezione.
        if self._selected_face_id is not None:
            candidate = self._selection_service.get_candidate(self._selected_face_id)
        if candidate is None:
            menu = QMenu(self)
            info = menu.addAction("Nessuna faccia selezionata")
            info.setEnabled(False)
            menu.addSeparator()
            menu.addAction("Deseleziona tutte le facce", self._deselect_all_faces)
            menu.addAction("Deseleziona faccia corrente", self._clear_face_selection)
            menu.addAction("Rimuovi tutte le assegnazioni manuali", self._clear_all_manual_assignments)
            menu.exec(global_pos)
            return
        self._show_face_context_menu(candidate, global_pos)

    def _show_face_context_menu(self, candidate, global_pos):
        """Build and show the context menu for a specific selected face."""
        menu = QMenu(self)
        info = menu.addAction(
            f"Faccia #{candidate.face_id} | {candidate.surface_type} | accessibile={'si' if candidate.accessible_from_top else 'no'}"
        )
        info.setEnabled(False)

        auto_types = self._auto_feature_types_for_face(candidate.face)
        if auto_types:
            auto_info = menu.addAction(f"Feature auto: {', '.join(auto_types)}")
            auto_info.setEnabled(False)

        manual_type = self._manual_face_assignments.get(candidate.face_id)
        if manual_type:
            manual_info = menu.addAction(f"Feature manuale: {manual_type}")
            manual_info.setEnabled(False)

        menu.addSeparator()
        assign_menu = menu.addMenu("Assegna feature compatibile")
        if candidate.compatible_features:
            for feature_type in candidate.compatible_features:
                action = assign_menu.addAction(feature_type)
                action.triggered.connect(lambda checked=False, fid=candidate.face_id, ftype=feature_type: self._assign_manual_feature(fid, ftype))
        else:
            no_action = assign_menu.addAction("Nessuna feature compatibile")
            no_action.setEnabled(False)

        remove_action = menu.addAction("Rimuovi assegnazione manuale")
        remove_action.setEnabled(candidate.face_id in self._manual_face_assignments)
        remove_action.triggered.connect(lambda: self._remove_manual_feature(candidate.face_id))

        if len(self._selected_face_ids) >= 2:
            menu.addSeparator()
            bulk_hole = menu.addMenu("Assegna foro a facce selezionate")
            can_hole = self._selection_service.can_assign_manual_hole(
                sorted(self._selected_face_ids),
                accessible_only=self._accessible_only,
            ) if self._selection_service else False
            if can_hole:
                bulk_hole.addAction("Foro passante", lambda: self._assign_hole_to_selected(True))
                bulk_hole.addAction("Foro cieco", lambda: self._assign_hole_to_selected(False))
            else:
                no_action = bulk_hole.addAction("Selezione non compatibile per foro")
                no_action.setEnabled(False)

        menu.addAction("Deseleziona tutte le facce", self._deselect_all_faces)
        menu.addAction("Deseleziona faccia corrente", self._clear_face_selection)
        menu.addAction("Rimuovi tutte le assegnazioni manuali", self._clear_all_manual_assignments)

        toggle_filter = menu.addAction(
            "Mostra tutte le facce" if self._accessible_only else "Mostra solo facce accessibili dall'alto"
        )
        toggle_filter.triggered.connect(self._toggle_accessible_filter)
        menu.exec(global_pos)

    def _assign_hole_to_selected(self, through: bool):
        """Assign hole semantics to all currently selected compatible faces."""
        if not self._selection_service or len(self._selected_face_ids) < 2:
            return
        selected_ids = sorted(self._selected_face_ids)
        if not self._selection_service.can_assign_manual_hole(selected_ids, accessible_only=self._accessible_only):
            self.statusBar().showMessage("Selezione non valida per assegnazione foro")
            return
        feature_type = "hole_through" if through else "hole_blind"
        for fid in selected_ids:
            self._manual_face_assignments[fid] = feature_type
            self._refresh_manual_face_overlay(fid)
        self.statusBar().showMessage(
            f"Assegnato {('foro passante' if through else 'foro cieco')} a {len(selected_ids)} facce"
        )

    def _assign_manual_feature(self, face_id: int, feature_type: str):
        """Assign one manual feature type to a face and refresh its overlay."""
        candidate = self._selection_service.get_candidate(face_id) if self._selection_service else None
        if candidate is None:
            return
        self._manual_face_assignments[face_id] = feature_type
        self._refresh_manual_face_overlay(face_id)
        self._select_face_candidate(candidate)
        logger.info("Feature manuale assegnata: faccia #%d -> %s", face_id, feature_type)

    def _remove_manual_feature(self, face_id: int):
        """Remove manual feature assignment and related overlays for one face."""
        self._manual_face_assignments.pop(face_id, None)
        overlay = self._manual_face_ais.pop(face_id, None)
        if overlay is not None:
            fill = overlay.get("fill")
            wire = overlay.get("wire")
            if fill is not None:
                self.ocp_widget.remove_interactive(fill)
            if wire is not None:
                self.ocp_widget.remove_interactive(wire)
        candidate = self._selection_service.get_candidate(face_id) if self._selection_service else None
        if candidate is not None:
            self._select_face_candidate(candidate)

    def _refresh_manual_face_overlay(self, face_id: int):
        """Rebuild colored wire overlay representing manual assignment state."""
        candidate = self._selection_service.get_candidate(face_id) if self._selection_service else None
        if candidate is None:
            return
        old_overlay = self._manual_face_ais.pop(face_id, None)
        if old_overlay is not None:
            fill = old_overlay.get("fill")
            wire = old_overlay.get("wire")
            if fill is not None:
                self.ocp_widget.remove_interactive(fill)
            if wire is not None:
                self.ocp_widget.remove_interactive(wire)
        color = self._feature_color(self._manual_face_assignments.get(face_id, ""))
        wire = self.ocp_widget.display_wire(
            candidate.face,
            color=color,
            width=2.5,
            selectable=False,
        )
        self._manual_face_ais[face_id] = {"fill": None, "wire": wire}

    def _auto_feature_types_for_face(self, face_shape) -> List[str]:
        matches = []
        for feat in self.features:
            geometry = getattr(feat, "geometry", None)
            if geometry is None:
                continue
            try:
                if geometry.IsSame(face_shape):
                    matches.append(feat.type)
            except Exception:
                continue
        return matches

    def _feature_color(self, feature_type: str):
        colors = {
            "pocket": (0.0, 0.3, 1.0),
            "step": (0.0, 0.6, 1.0),
            "opening": (0.0, 1.0, 1.0),
            "fillet": (0.0, 1.0, 0.0),
            "chamfer": (1.0, 1.0, 0.0),
            "countersunk_hole": (1.0, 0.3, 0.0),
            "hole": (1.0, 0.0, 0.0),
            "hole_through": (1.0, 0.0, 1.0),
            "hole_blind": (1.0, 0.0, 0.0),
        }
        return colors.get(feature_type, (1.0, 0.0, 1.0))

    def _display_toolpath_plan(self):
        if self._toolpath_plan is None:
            return

        for overlay in self._toolpath_ais:
            self.ocp_widget.remove_interactive(overlay)
        self._toolpath_ais.clear()

        for operation in getattr(self._toolpath_plan, "operations", []):
            if not self._should_display_toolpath_operation(operation):
                continue
            color, width = self._toolpath_display_style(operation)
            for shape in self._build_operation_display_shapes(operation):
                overlay = self.ocp_widget.display_wire(
                    shape,
                    color=color,
                    width=width,
                    selectable=False,
                )
                self._toolpath_ais.append(overlay)

    def _should_display_toolpath_operation(self, operation) -> bool:
        if self._toolpath_mode_filter == "all":
            return True
        return self._toolpath_operation_mode(operation) == self._toolpath_mode_filter

    def _toolpath_filter_label(self) -> str:
        labels = {
            "all": "tutte",
            "drilling": "solo drilling",
            "roughing": "solo roughing",
            "finishing": "solo finishing",
        }
        return labels.get(self._toolpath_mode_filter, self._toolpath_mode_filter)

    def _toolpath_operation_mode(self, operation) -> str:
        return str(getattr(operation, "metadata", {}).get("operation_mode", "")).lower()

    @staticmethod
    def _toolpath_style_catalog():
        return {
            "drilling": {"label": "drill", "color": (1.0, 0.2, 1.0), "width": 2.6},
            "slot_roughing": {"label": "slot rough", "color": (0.1, 0.45, 1.0), "width": 3.2},
            "slot_finishing": {"label": "slot finish", "color": (0.0, 0.95, 1.0), "width": 2.4},
            "cavity_roughing": {"label": "cavity rough", "color": (1.0, 0.6, 0.0), "width": 3.3},
            "cavity_finishing": {"label": "cavity finish", "color": (0.15, 1.0, 0.35), "width": 2.4},
            "profile_roughing": {"label": "profile rough", "color": (1.0, 0.35, 0.0), "width": 2.9},
            "profile_finishing": {"label": "profile finish", "color": (1.0, 0.95, 0.1), "width": 2.2},
            "roughing": {"label": "rough", "color": (1.0, 0.55, 0.0), "width": 3.0},
            "finishing": {"label": "finish", "color": (0.1, 1.0, 0.35), "width": 2.0},
            "default": {"label": "other", "color": (1.0, 1.0, 1.0), "width": 2.0},
        }

    @staticmethod
    def _toolpath_color_hex(color) -> str:
        return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(channel * 255.0)))) for channel in color)

    def _toolpath_style_key(self, operation) -> str:
        strategy = str(getattr(operation, "strategy", "") or "").lower()
        mode = self._toolpath_operation_mode(operation)
        if mode == "drilling" or strategy == "drilling":
            return "drilling"
        if strategy == "slot_milling":
            if mode == "roughing":
                return "slot_roughing"
            if mode == "finishing":
                return "slot_finishing"
        if strategy == "cavity_clearing":
            if mode == "roughing":
                return "cavity_roughing"
            if mode == "finishing":
                return "cavity_finishing"
        if strategy == "2p5d_profile":
            if mode == "roughing":
                return "profile_roughing"
            if mode == "finishing":
                return "profile_finishing"
        if mode == "roughing":
            return "roughing"
        if mode == "finishing":
            return "finishing"
        return "default"

    def _toolpath_legend_html(self) -> str:
        catalog = self._toolpath_style_catalog()
        legend_keys = (
            "drilling",
            "slot_roughing",
            "slot_finishing",
            "cavity_roughing",
            "cavity_finishing",
            "profile_roughing",
            "profile_finishing",
        )
        parts = []
        for key in legend_keys:
            entry = catalog[key]
            color = self._toolpath_color_hex(entry["color"])
            parts.append(f"<span style='color:{color}; font-weight:600'>{entry['label']}</span>")
        return "Toolpath: " + " | ".join(parts)

    def _toolpath_display_style(self, operation):
        entry = self._toolpath_style_catalog()[self._toolpath_style_key(operation)]
        return entry["color"], entry["width"]

    def _build_operation_display_shapes(self, operation):
        cut_feed = getattr(operation, "metadata", {}).get("cut_feed")
        return [
            wire
            for path_points in self._split_toolpath_motion_paths(getattr(operation, "motions", []), cut_feed=cut_feed)
            for wire in [self._make_polyline_wire(path_points)]
            if wire is not None
        ]

    def _split_toolpath_motion_paths(self, motions, *, cut_feed=None):
        paths: List[List[np.ndarray]] = []
        current_path: List[np.ndarray] = []
        previous_point: Optional[np.ndarray] = None
        cut_feed_value: Optional[float] = None
        if cut_feed is not None:
            try:
                cut_feed_value = float(cut_feed)
            except (TypeError, ValueError):
                cut_feed_value = None

        for motion in motions:
            point = np.array(getattr(motion, "point", ()), dtype=float)
            if point.shape != (3,):
                continue

            move = getattr(motion, "move", "")
            feed = getattr(motion, "feed", None)
            is_cut_move = move == "linear"
            if is_cut_move and cut_feed_value is not None:
                if feed is None:
                    is_cut_move = False
                else:
                    # Operation metadata rounds feeds to 4 decimals; keep the overlay tolerant to that loss.
                    is_cut_move = bool(np.isclose(float(feed), cut_feed_value, atol=1e-4, rtol=1e-6))

            if not is_cut_move:
                if len(current_path) >= 2:
                    paths.append(current_path)
                current_path = []
                previous_point = point
                continue

            if not current_path:
                if previous_point is not None:
                    current_path.append(np.array(previous_point, dtype=float))
                current_path.append(point)
            elif np.linalg.norm(current_path[-1] - point) > 1e-7:
                current_path.append(point)

            previous_point = point

        if len(current_path) >= 2:
            paths.append(current_path)
        return paths

    def _make_polyline_wire(self, points):
        try:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
            from OCP.gp import gp_Pnt
        except Exception:
            return None

        unique_points: List[np.ndarray] = []
        for point in points:
            point_np = np.array(point, dtype=float)
            if not unique_points or np.linalg.norm(unique_points[-1] - point_np) > 1e-7:
                unique_points.append(point_np)

        if len(unique_points) < 2:
            return None

        polygon = BRepBuilderAPI_MakePolygon()
        for point in unique_points:
            polygon.Add(gp_Pnt(float(point[0]), float(point[1]), float(point[2])))
        if np.linalg.norm(unique_points[0] - unique_points[-1]) <= 1e-6:
            polygon.Close()
        return polygon.Wire()

    def _mesh_to_ocp_shape(self, mesh):
        """Converte una mesh trimesh in una shape OCP."""
        try:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Sewing
            from OCP.gp import gp_Pnt
            
            logger.info(f"Conversione mesh STL a OCP: {len(mesh.faces)} triangoli")
            faces = []
            for face_indices in mesh.faces:
                v0, v1, v2 = [mesh.vertices[vi] for vi in face_indices]
                poly = BRepBuilderAPI_MakePolygon(gp_Pnt(*v0), gp_Pnt(*v1), gp_Pnt(*v2), True)
                f = BRepBuilderAPI_MakeFace(poly.Wire(), True)
                if f.IsDone():
                    faces.append(f.Face())
            
            if not faces:
                logger.error("Nessuna faccia creata dalla mesh")
                return None
            
            # Cuci insieme le facce
            sewing = BRepBuilderAPI_Sewing(1e-2)
            for f in faces:
                sewing.Add(f)
            sewing.Perform()
            result = sewing.SewedShape()
            logger.info(f"Shape creata da mesh: {len(faces)} facce")
            return result
        except Exception as e:
            logger.error(f"Errore conversione mesh: {e}")
            return None

    def _make_circle_wire(self, hole_feature) -> Optional[TopoDS_Shape]:
        """Costruisce un cerchio completo sul piano z-min per un foro passante."""
        try:
            from OCP.gp import gp_Ax2, gp_Circ
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
            from OCP.BRepAdaptor import BRepAdaptor_Surface
            from OCP.GeomAbs import GeomAbs_Cylinder
            from OCP.TopoDS import TopoDS

            face = hole_feature.geometry
            surf = BRepAdaptor_Surface(TopoDS.Face_s(face), True)
            if surf.GetType() != GeomAbs_Cylinder:
                return self._extract_zmin_wire(face)

            cyl = surf.Cylinder()
            radius = cyl.Radius()
            loc = cyl.Axis().Location()
            n = self.working_plane_normal

            # Proietta il centro sul piano z-min
            from OCP.BRepBndLib import BRepBndLib
            from OCP.Bnd import Bnd_Box
            bbox = Bnd_Box()
            shape_to_use = self.model.brep or (self._mesh_to_ocp_shape(self.model.mesh) if self.model.mesh else None)
            if not shape_to_use:
                return self._extract_zmin_wire(face)
            BRepBndLib.Add_s(shape_to_use, bbox)
            xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
            import numpy as np
            corners = [np.array([xmin,ymin,zmin]),np.array([xmax,ymin,zmin]),
                       np.array([xmin,ymax,zmin]),np.array([xmax,ymax,zmin]),
                       np.array([xmin,ymin,zmax]),np.array([xmax,ymin,zmax]),
                       np.array([xmin,ymax,zmax]),np.array([xmax,ymax,zmax])]
            z_min = min(np.dot(c, n) for c in corners)

            proj = loc.X()*n[0] + loc.Y()*n[1] + loc.Z()*n[2]
            delta = z_min - proj
            center = gp_Pnt(loc.X()+n[0]*delta, loc.Y()+n[1]*delta, loc.Z()+n[2]*delta)

            ax2 = gp_Ax2(center, gp_Dir(n[0], n[1], n[2]))
            circle = gp_Circ(ax2, radius)
            edge = BRepBuilderAPI_MakeEdge(circle).Edge()
            wire = BRepBuilderAPI_MakeWire(edge).Wire()
            return wire
        except Exception:
            return self._extract_zmin_wire(hole_feature.geometry)

    def _extract_zmin_wire(self, face_shape) -> Optional[TopoDS_Shape]:
        """Estrae il wire con proiezione minima sull'asse utensile (fondo del foro/asola)."""
        from OCP.BRep import BRep_Tool
        from OCP.TopoDS import TopoDS
        from OCP.BRepAdaptor import BRepAdaptor_Curve

        best_wire = None
        best_proj = float('inf')
        n = self.working_plane_normal

        exp_w = TopExp_Explorer(face_shape, TopAbs_WIRE)
        while exp_w.More():
            wire = exp_w.Current()
            proj_vals = []
            exp_e = TopExp_Explorer(wire, TopAbs_EDGE)
            while exp_e.More():
                curve = BRepAdaptor_Curve(TopoDS.Edge_s(exp_e.Current()))
                t = (curve.FirstParameter() + curve.LastParameter()) / 2
                p = curve.Value(t)
                proj_vals.append(p.X()*n[0] + p.Y()*n[1] + p.Z()*n[2])
                exp_e.Next()
            if proj_vals:
                proj_mean = sum(proj_vals) / len(proj_vals)
                if proj_mean < best_proj:
                    best_proj = proj_mean
                    best_wire = wire
            exp_w.Next()

        return best_wire

def show_model_with_features(
    model,
    features,
    working_plane_normal=(0.0, 0.0, 1.0),
    contour_shadow=None,
    perimeter=None,
    vertical_arc_groups=None,
    toolpath_plan=None,
    *,
    enable_face_selection: bool = True,
    show_toolpath_legend: bool = True,
    show_model_edges: bool = False,
    model_edge_color=(0.18, 0.18, 0.18),
    model_edge_width: float = 1.5,
    status_message: Optional[str] = None,
):
    app = QApplication.instance() or QApplication(sys.argv)
    window = AntCamViewerWindow(
        model,
        features,
        working_plane_normal,
        contour_shadow=contour_shadow,
        perimeter=perimeter,
        vertical_arc_groups=vertical_arc_groups,
        toolpath_plan=toolpath_plan,
        enable_face_selection=enable_face_selection,
        show_toolpath_legend=show_toolpath_legend,
        show_model_edges=show_model_edges,
        model_edge_color=model_edge_color,
        model_edge_width=model_edge_width,
        status_message=status_message,
    )
    window.show()
    sys.exit(app.exec())

