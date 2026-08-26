from __future__ import annotations

import ctypes
import math
import sys
from typing import Optional

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QResizeEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMainWindow, QStatusBar, QWidget

from OCP.AIS import AIS_InteractiveContext, AIS_Shape, AIS_Shaded, AIS_WireFrame
from OCP.Aspect import Aspect_DisplayConnection, Aspect_GradientFillMethod
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
from OCP.GeomAbs import GeomAbs_Plane
from OCP.gp import gp_Pnt, gp_Dir, gp_Vec, gp_Trsf, gp_Ax1, gp_Quaternion
from OCP.OpenGl import OpenGl_GraphicDriver
from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape
from OCP.V3d import V3d_Viewer, V3d_TypeOfOrientation
from OCP.WNT import WNT_Window

from antcam.feature_extractor import FeatureExtractor
from antcam.importer import Model
from antcam.theme import load_theme, occ_color, SEARCH_PATHS

theme = None
for p in SEARCH_PATHS:
    if p.exists():
        theme = load_theme(p)
        break
if theme is None:
    theme = load_theme()


_HIGHLIGHT_COLOR = occ_color(theme["features"]["highlight_color"])
_HIGHLIGHT_ALPHA = theme["features"]["highlight_transparency"]


def _get_capsule_from_int(handle: int):
    ctypes.pythonapi.PyCapsule_New.restype = ctypes.py_object
    ctypes.pythonapi.PyCapsule_New.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
    return ctypes.pythonapi.PyCapsule_New(handle, None, None)


def _model_bounds(model: Model) -> tuple[float, float, float, float, float, float]:
    box = Bnd_Box()
    if not model.is_mesh_only:
        BRepBndLib.Add_s(model.shape, box)
    elif model.mesh is not None:
        for p in model.mesh.vertices:
            box.Update(*p.tolist())
    return box.Get()


def _grid_size_from_bounds(model: Model) -> float:
    g = theme["grid"]
    xmin, ymin, _, xmax, ymax, _ = _model_bounds(model)
    extent = max(xmax - xmin, ymax - ymin, 20.0) + 2 * g["margin_mm"]
    return math.ceil(extent / g["major_step_mm"]) * g["major_step_mm"]


def _build_grid(size: float) -> tuple[TopoDS_Compound, TopoDS_Compound]:
    minor_builder = BRep_Builder()
    minor_comp = TopoDS_Compound()
    minor_builder.MakeCompound(minor_comp)

    major_builder = BRep_Builder()
    major_comp = TopoDS_Compound()
    major_builder.MakeCompound(major_comp)

    g = theme["grid"]
    major_step = g["major_step_mm"]
    minor_step = g["minor_step_mm"]
    half = size / 2.0
    n = int(size / minor_step) // 2

    for i in range(-n, n + 1):
        v = i * minor_step
        if abs(v) > half + 1e-9:
            continue
        is_major = abs(v % major_step) < 1e-9
        builder = major_builder if is_major else minor_builder
        comp = major_comp if is_major else minor_comp
        p1 = gp_Pnt(v, -half, 0.0)
        p2 = gp_Pnt(v, half, 0.0)
        p3 = gp_Pnt(-half, v, 0.0)
        p4 = gp_Pnt(half, v, 0.0)
        for a, b in [(p1, p2), (p3, p4)]:
            edge = BRepBuilderAPI_MakeEdge(a, b).Edge()
            builder.Add(comp, edge)

    return minor_comp, major_comp


def _iter_faces(shape: TopoDS_Shape) -> list[TopoDS_Shape]:
    faces: list[TopoDS_Shape] = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        faces.append(exp.Current())
        exp.Next()
    return faces


def _face_hash(s: TopoDS_Shape) -> int:
    return hash(s)


class QOCPWidget(QWidget):
    face_detected = Signal()
    face_double_clicked = Signal()
    escape_pressed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._initialized = False
        self._ctx = None
        self._view = None
        self._last_pos = QPoint()
        self._left_press_pos = QPoint()
        self._left_dragging = False
        self.setMouseTracking(True)
        self._screen_connected = False

        # Refresh timer — keeps canvas alive across resize, monitor switch,
        # and focus loss without fighting buffer re-allocation.  Fires on the
        # Qt event-loop thread so Redraw() always sees a stable backing buffer.
        # Rate is configurable via theme["rendering"]["refresh_fps"].
        fps = theme.get("rendering", {}).get("refresh_fps", 20)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._redraw)
        self._refresh_timer.start(int(1000 / max(fps, 1)))

        QTimer(self).singleShot(0, self._init_ocp)

    def paintEngine(self) -> None:
        return None

    def _init_ocp(self) -> None:
        if self._initialized:
            return
        display = Aspect_DisplayConnection()
        driver = OpenGl_GraphicDriver(display)
        viewer = V3d_Viewer(driver)
        viewer.SetDefaultLights()
        viewer.SetLightOn()

        view = viewer.CreateView()

        rp = view.ChangeRenderingParams()
        rp.NbMsaaSamples = theme["rendering"]["msaa_samples"]

        bg = theme["background"]
        c1 = occ_color(bg["gradient_top"])
        c2 = occ_color(bg["gradient_bottom"])
        view.SetBgGradientColors(c1, c2, Aspect_GradientFillMethod.Aspect_GradientFillMethod_Vertical, True)

        ctx = AIS_InteractiveContext(viewer)

        handle = int(self.winId())
        window_handle = WNT_Window(_get_capsule_from_int(handle))
        view.SetWindow(window_handle)
        if not window_handle.IsMapped():
            window_handle.Map()
        view.MustBeResized()

        self._viewer = viewer
        self._view = view
        self._ctx = ctx
        self._initialized = True

        if not self._screen_connected:
            w = self.windowHandle()
            if w:
                w.screenChanged.connect(self._on_screen_changed)
                self._screen_connected = True

    def _redraw(self) -> None:
        if self._initialized:
            self._view.Redraw()

    def resizeEvent(self, event: QResizeEvent) -> None:
        if self._initialized:
            self._view.MustBeResized()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        if not self._initialized:
            self._init_ocp()
        self._redraw()

    def mousePressEvent(self, event) -> None:
        self._last_pos = event.position().toPoint()
        self._left_press_pos = self._last_pos
        self._left_dragging = False
        if event.button() == Qt.MouseButton.LeftButton and self._initialized:
            self._view.StartRotation(self._last_pos.x(), self._last_pos.y())

    def mouseMoveEvent(self, event) -> None:
        if not self._initialized:
            return
        pos = event.position().toPoint()
        if event.buttons() == Qt.MouseButton.NoButton:
            self._ctx.MoveTo(pos.x(), pos.y(), self._view, True)
            self.face_detected.emit()
        elif event.buttons() & Qt.MouseButton.LeftButton:
            if (pos - self._left_press_pos).manhattanLength() > 3:
                self._left_dragging = True
            self._view.Rotation(pos.x(), pos.y())
        elif event.buttons() & Qt.MouseButton.MiddleButton:
            self._view.Pan(pos.x() - self._last_pos.x(), self._last_pos.y() - pos.y())
        self._last_pos = pos

    def mouseReleaseEvent(self, event) -> None:
        self._left_dragging = False
        self._last_pos = event.position().toPoint()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._initialized:
            self._ctx.MoveTo(int(event.position().x()), int(event.position().y()), self._view, True)
            self.face_double_clicked.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        if not self._initialized:
            return
        self._view.SetZoom(1.1 if event.angleDelta().y() > 0 else 0.9)

    def closeEvent(self, event) -> None:
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        if self._initialized:
            self._view.MustBeResized()
        super().showEvent(event)

    def moveEvent(self, event) -> None:
        if self._initialized:
            self._view.MustBeResized()
        super().moveEvent(event)

    def _on_screen_changed(self) -> None:
        if self._initialized:
            self._view.MustBeResized()


class AntCamViewerWindow(QMainWindow):
    def __init__(self, model: Model) -> None:
        super().__init__()
        self.model = model
        self._ais_shapes: list[AIS_Shape] = []
        self._grid_ais: list[AIS_Shape] = []
        self._edges_ais: list[AIS_Shape] = []
        self._model_shown = False
        self._features: list = []
        self._face_sid_to_idx: dict[int, int] = {}
        self._face_idx_to_feature: dict[int, set[int]] = {}
        self._highlight_ais: AIS_Shape | None = None
        self._hovered_sid: int = -1

        self._selecting_bottom = False

        self.setWindowTitle(f"AntCAM — {model.source_path.name}")
        self.resize(1200, 800)
        self.setStyleSheet(theme["ui"]["qss"])

        self._setup_ui()
        self._setup_menu()
        self._setup_statusbar()

        QTimer(self).singleShot(100, self._display_model)

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.occ_widget = QOCPWidget()
        self.occ_widget.face_detected.connect(self._on_face_detected)
        self.occ_widget.face_double_clicked.connect(self._on_face_double_clicked)
        self.occ_widget.escape_pressed.connect(self._on_escape)
        layout.addWidget(self.occ_widget, 1)

    def _setup_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        file_menu.addAction("&Open STEP...", self._on_open)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close, QKeySequence.Quit)

        view_menu = menubar.addMenu("&View")

        self._edges_action = QAction("Show &Edges", self)
        self._edges_action.setCheckable(True)
        self._edges_action.setChecked(True)
        self._edges_action.triggered.connect(self._toggle_edges)
        view_menu.addAction(self._edges_action)

        self._grid_action = QAction("Show &Grid", self)
        self._grid_action.setCheckable(True)
        self._grid_action.setChecked(True)
        self._grid_action.triggered.connect(self._toggle_grid)
        view_menu.addAction(self._grid_action)

        view_menu.addSeparator()
        view_menu.addAction("&Fit All\tCtrl+F", self._fit_all, QKeySequence("Ctrl+F"))

        orientation_menu = view_menu.addMenu("&Orientation")
        for name, orient in [
            ("Front", V3d_TypeOfOrientation.V3d_Xpos),
            ("Back", V3d_TypeOfOrientation.V3d_Xneg),
            ("Top", V3d_TypeOfOrientation.V3d_Zpos),
            ("Bottom", V3d_TypeOfOrientation.V3d_Zneg),
            ("Left", V3d_TypeOfOrientation.V3d_Ypos),
            ("Right", V3d_TypeOfOrientation.V3d_Yneg),
            ("Axonometric", V3d_TypeOfOrientation.V3d_XposYnegZpos),
        ]:
            action = QAction(name, self)
            action.triggered.connect(lambda _, o=orient: self._set_orientation(o))
            orientation_menu.addAction(action)

        tools_menu = menubar.addMenu("&Tools")
        self._bottom_action = QAction("Set &Bottom Surface", self)
        self._bottom_action.triggered.connect(self._start_bottom_selection)
        tools_menu.addAction(self._bottom_action)

    def _setup_statusbar(self) -> None:
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self._hover_label = QLabel()
        self._hover_label.setStyleSheet("color: #a0a0a0; font-size: 11px; padding: 0 8px;")
        self.status.addPermanentWidget(self._hover_label)
        self._update_status()

    def _update_status(self, msg: str = "") -> None:
        self.status.showMessage(f"Faces: {self.model.face_count()} — Features: {len(self._features)}")
        self._hover_label.setText(msg)

    def _on_open(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Open STEP/STL", "", "STEP (*.step *.stp);;STL (*.stl)")
        if path:
            from antcam.importer import Model as M
            import os
            ext = os.path.splitext(path)[1].lower()
            m = M.from_step(path) if ext in (".step", ".stp") else M.from_stl(path) if ext == ".stl" else None
            if m is None:
                return
            self.model = m
            self.setWindowTitle(f"AntCAM — {m.source_path.name}")
            self._clear_shapes()
            self._display_model()

    def _clear_shapes(self) -> None:
        ctx = self.occ_widget._ctx
        if ctx:
            for ais in self._ais_shapes + self._grid_ais + self._edges_ais:
                ctx.Remove(ais, True)
            if self._highlight_ais:
                ctx.Remove(self._highlight_ais, True)
                self._highlight_ais = None
        self._ais_shapes.clear()
        self._grid_ais.clear()
        self._edges_ais.clear()
        self._features.clear()
        self._face_sid_to_idx.clear()
        self._face_idx_to_feature.clear()
        self._hovered_sid = -1
        self._hover_label.setText("")
        self._model_shown = False

    def _set_orientation(self, orient) -> None:
        if self.occ_widget._initialized:
            self.occ_widget._view.SetProj(orient)
            self.occ_widget._view.Redraw()

    def _fit_all(self) -> None:
        if self.occ_widget._initialized:
            self.occ_widget._view.FitAll()
            self.occ_widget._view.Redraw()

    def _toggle_edges(self, visible: bool) -> None:
        ctx = self.occ_widget._ctx
        if ctx is None:
            return
        for ais in self._edges_ais:
            (ctx.Display if visible else ctx.Remove)(ais, True)
        self.occ_widget._view.Redraw()

    def _toggle_grid(self, visible: bool) -> None:
        ctx = self.occ_widget._ctx
        if ctx is None:
            return
        for ais in self._grid_ais:
            (ctx.Display if visible else ctx.Remove)(ais, True)
        self.occ_widget._view.Redraw()

    def _extract_features_and_mapping(self) -> None:
        extractor = FeatureExtractor()
        self._features = extractor.extract(self.model.shape)

        raw_faces = _iter_faces(self.model.shape)
        for idx, raw_shape in enumerate(raw_faces):
            sid = _face_hash(raw_shape)
            self._face_sid_to_idx[sid] = idx
        for fi in range(len(raw_faces)):
            self._face_idx_to_feature.setdefault(fi, set())

        for feati, feat in enumerate(self._features):
            for fi in feat.face_indices:
                if 0 <= fi < len(raw_faces):
                    self._face_idx_to_feature[fi].add(feati)

    def _detected_face_sid(self) -> int:
        ctx = self.occ_widget._ctx
        if not ctx or not ctx.HasDetected():
            return -1
        shape = ctx.DetectedShape()
        if shape is None or shape.ShapeType() != TopAbs_FACE:
            return -1
        return _face_hash(shape)

    def _feat_summary(self, feati: int) -> str:
        f = self._features[feati]
        t = f.type
        p = f.props
        if t == "hole_group":
            d = p.get("diameter", 0)
            dp = p.get("depth", 0)
            th = "through" if p.get("through") else "blind"
            return f"Hole ∅{d:.1f} × {dp:.1f}mm {th} (×{p.get('count',1)})"
        if t == "hole":
            d = p.get("diameter", 0)
            dp = p.get("depth", 0)
            th = "through" if p.get("through") else "blind"
            return f"Hole ∅{d:.1f} × {dp:.1f}mm {th}"
        if t == "slot":
            return f"Slot w{p.get('width',0):.1f} × l{p.get('length',0):.1f} × d{p.get('depth',0):.1f}mm"
        if t == "pocket":
            return f"Pocket depth {p.get('depth',0):.1f}mm area {p.get('area',0):.0f}mm²"
        if t == "step":
            return f"Step depth {p.get('depth',0):.1f}mm"
        if t == "fillet":
            return f"Fillet R{p.get('radius',0):.1f}mm"
        if t == "chamfer":
            a = p.get("angle", 0)
            return f"Chamfer {a:.0f}°"
        if t == "perimeter":
            return f"Outer Perimeter ({p.get('face_count',0)} walls)"
        return t.title()

    def _on_face_detected(self) -> None:
        detected_sid = self._detected_face_sid()
        if detected_sid == self._hovered_sid:
            return
        self._hovered_sid = detected_sid
        self._clear_highlight()
        if detected_sid < 0:
            ctx = self.occ_widget._ctx
            has = ctx.HasDetected() if ctx else False
            st = -1
            if has:
                try:
                    st = ctx.DetectedShape().ShapeType()
                except Exception:
                    pass
            self._update_status(f"HasDetected={has} type={st}")
            return
        fi = self._face_sid_to_idx.get(detected_sid, -1)
        if fi < 0:
            self._update_status(f"hash={detected_sid} not in map")
            return
        feat_indices = self._face_idx_to_feature.get(fi, set())
        if not feat_indices:
            self._update_status(f"face #{fi} has no feature")
            return
        labels = [self._feat_summary(fi) for fi in feat_indices]
        self._update_status("  |  ".join(labels))
        self._highlight_features(feat_indices)

    def _highlight_features(self, feat_indices: set[int]) -> None:
        if self._highlight_ais:
            self.occ_widget._ctx.Remove(self._highlight_ais, True)
            self._highlight_ais = None

        raw_faces = _iter_faces(self.model.shape)
        face_idxs: set[int] = set()
        for fi, feat_set in self._face_idx_to_feature.items():
            if feat_set & feat_indices:
                face_idxs.add(fi)

        if not face_idxs:
            return

        builder = BRep_Builder()
        comp = TopoDS_Compound()
        builder.MakeCompound(comp)
        for fi in face_idxs:
            if fi < len(raw_faces):
                builder.Add(comp, raw_faces[fi])

        self._highlight_ais = AIS_Shape(comp)
        self._highlight_ais.SetDisplayMode(AIS_Shaded)
        self._highlight_ais.SetColor(_HIGHLIGHT_COLOR)
        self._highlight_ais.SetTransparency(_HIGHLIGHT_ALPHA)
        self.occ_widget._ctx.Display(self._highlight_ais, True)
        self.occ_widget._view.Redraw()

    def _clear_highlight(self) -> None:
        if self._highlight_ais:
            self.occ_widget._ctx.Remove(self._highlight_ais, True)
            self._highlight_ais = None
            self.occ_widget._view.Redraw()

    def _start_bottom_selection(self) -> None:
        self._selecting_bottom = True
        self._bottom_action.setEnabled(False)
        self._update_status("Double-click a face to set as bottom plane, ESC to cancel")

    def _on_escape(self) -> None:
        if self._selecting_bottom:
            self._selecting_bottom = False
            self._bottom_action.setEnabled(True)
            self._update_status("Cancelled")

    def _on_face_double_clicked(self) -> None:
        if not self._selecting_bottom:
            return
        sid = self._detected_face_sid()
        if sid < 0:
            return
        fi = self._face_sid_to_idx.get(sid, -1)
        if fi < 0:
            return
        self._selecting_bottom = False
        self._bottom_action.setEnabled(True)
        self._update_status("Rotating ...")
        self._set_bottom_from_face(fi)

    def _set_bottom_from_face(self, fi: int) -> None:
        raw_faces = _iter_faces(self.model.shape)
        if fi >= len(raw_faces):
            return
        face_raw = raw_faces[fi]
        face = TopoDS.Face_s(face_raw)
        surf = BRepAdaptor_Surface(face)
        st = surf.GetType()
        if st != GeomAbs_Plane:
            self._update_status("Selected face is not planar — pick a flat face")
            return
        normal = surf.Plane().Axis().Direction()
        target = gp_Dir(0, 0, 1)
        if normal.IsEqual(target, 0.001):
            self._update_status("Face is already horizontal — no rotation needed")
            self._reload_model()
            return
        quat = gp_Quaternion(gp_Vec(normal), gp_Vec(target))
        trsf = gp_Trsf()
        trsf.SetRotation(quat)
        transform = BRepBuilderAPI_Transform(self.model.shape, trsf, True)
        self.model.shape = transform.Shape()
        self._reload_model()

    def _reload_model(self) -> None:
        self._clear_shapes()
        QTimer(self).singleShot(10, self._display_model)

    def _display_model(self) -> None:
        if self.model is None:
            return
        if not self.occ_widget._initialized:
            QTimer(self).singleShot(100, self._display_model)
            return

        ctx = self.occ_widget._ctx
        view = self.occ_widget._view
        self._clear_shapes()

        self._extract_features_and_mapping()

        gs = _grid_size_from_bounds(self.model)
        minor_comp, major_comp = _build_grid(gs)

        mg = AIS_Shape(minor_comp)
        mg.SetDisplayMode(AIS_WireFrame)
        mg.SetColor(occ_color(theme["grid"]["minor_color"]))
        ctx.Display(mg, True)
        ctx.SetSelectionModeActive(mg, 0, False)
        self._grid_ais.append(mg)

        Mg = AIS_Shape(major_comp)
        Mg.SetDisplayMode(AIS_WireFrame)
        Mg.SetColor(occ_color(theme["grid"]["major_color"]))
        ctx.Display(Mg, True)
        ctx.SetSelectionModeActive(Mg, 0, False)
        self._grid_ais.append(Mg)

        body_color = occ_color(theme["model"]["body_color"])
        edge_color = occ_color(theme["model"]["edge_color"])

        if not self.model.is_mesh_only:
            a = AIS_Shape(self.model.shape)
            a.SetDisplayMode(AIS_Shaded)
            a.SetColor(body_color)
            ctx.Display(a, True)
            ctx.SetSelectionModeActive(a, int(TopAbs_FACE), True)
            self._ais_shapes.append(a)

            w = AIS_Shape(self.model.shape)
            w.SetDisplayMode(AIS_WireFrame)
            w.SetColor(edge_color)
            ctx.Display(w, True)
            ctx.SetSelectionModeActive(w, 0, False)
            self._edges_ais.append(w)
        else:
            mesh = self.model.mesh
            builder = BRep_Builder()
            compound = TopoDS_Compound()
            builder.MakeCompound(compound)
            for fv in mesh.faces:
                tri = mesh.vertices[fv]
                pts = [gp_Pnt(float(x), float(y), float(z)) for x, y, z in tri]
                poly = BRepBuilderAPI_MakePolygon()
                for pt in pts:
                    poly.Add(pt)
                poly.Close()
                fm = BRepBuilderAPI_MakeFace(poly.Wire(), True)
                if fm.IsDone():
                    builder.Add(compound, fm.Shape())
            a = AIS_Shape(compound)
            a.SetColor(body_color)
            ctx.Display(a, True)
            self._ais_shapes.append(a)

        view.FitAll()
        self._update_status("Ready")

        view.Redraw()
        self._model_shown = True

    def closeEvent(self, event) -> None:
        self._clear_shapes()
        super().closeEvent(event)


def launch_viewer(model: Model) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle(theme["ui"]["qt_style"])
    window = AntCamViewerWindow(model)
    window.show()
    sys.exit(app.exec())
