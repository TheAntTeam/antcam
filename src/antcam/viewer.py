import sys
import logging
import ctypes
import math
from typing import List, Optional

from PySide6.QtWidgets import QMainWindow, QWidget, QApplication
from PySide6.QtCore import Qt, QTimer, QPoint

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
from OCP.TopAbs import TopAbs_WIRE, TopAbs_EDGE
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
from OCP.Prs3d import Prs3d_LineAspect
from OCP.Aspect import Aspect_TOL_SOLID
import numpy as np

logger = logging.getLogger("antcam")

def get_capsule_from_int(handle: int):
    ctypes.pythonapi.PyCapsule_New.restype = ctypes.py_object
    ctypes.pythonapi.PyCapsule_New.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
    return ctypes.pythonapi.PyCapsule_New(handle, None, None)

class QOCPWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setAttribute(Qt.WA_PaintOnScreen)
        self.setAttribute(Qt.WA_NoSystemBackground)
        
        self._is_initialized = False
        self.context = None
        self.view = None
        self._last_pos = QPoint()

    def _init_ocp(self):
        if self._is_initialized: return
        try:
            display_connection = Aspect_DisplayConnection()
            graphic_driver = OpenGl_GraphicDriver(display_connection)
            viewer = V3d_Viewer(graphic_driver)
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

    def mousePressEvent(self, event):
        self._last_pos = event.position().toPoint()
        if event.button() == Qt.LeftButton:
            self.view.StartRotation(self._last_pos.x(), self._last_pos.y())

    def mouseMoveEvent(self, event):
        if not self._is_initialized: return
        pos = event.position().toPoint()
        if event.buttons() & Qt.LeftButton:
            self.view.Rotation(pos.x(), pos.y())
        elif event.buttons() & Qt.MiddleButton:
            self.view.Pan(pos.x() - self._last_pos.x(), self._last_pos.y() - pos.y())
        self._last_pos = pos
        self.view.Redraw()

    def wheelEvent(self, event):
        if not self._is_initialized: return
        zoom_factor = 1.1 if event.angleDelta().y() > 0 else 0.9
        self.view.SetZoom(zoom_factor)
        self.view.Redraw()

    def paintEvent(self, event):
        if not self._is_initialized: self._init_ocp()
        if self.view: self.view.Redraw()

    def resizeEvent(self, event):
        if self._is_initialized and self.view:
            self.view.MustBeResized()
            self.view.Redraw()

    def display_shape(self, shape: TopoDS_Shape, color=(0.7, 0.7, 0.7), transparency=0.0):
        if not self._is_initialized: self._init_ocp()
        ais_shape = AIS_Shape(shape)
        q_color = Quantity_Color(color[0], color[1], color[2], Quantity_TOC_RGB)
        ais_shape.SetColor(q_color)
        if transparency > 0: ais_shape.SetTransparency(transparency)
        self.context.Display(ais_shape, True)
        return ais_shape

    def display_shadow(self, shape: TopoDS_Shape, color=(1.0, 0.5, 0.0), transparency=0.3):
        """Visualizza uno shape in modalita' shaded semitrasparente."""
        if not self._is_initialized: self._init_ocp()
        from OCP.Graphic3d import Graphic3d_MaterialAspect, Graphic3d_NOM_PLASTIC
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        BRepMesh_IncrementalMesh(shape, 0.1)
        ais_shape = AIS_Shape(shape)
        q_color = Quantity_Color(color[0], color[1], color[2], Quantity_TOC_RGB)
        ais_shape.SetColor(q_color)
        mat = Graphic3d_MaterialAspect(Graphic3d_NOM_PLASTIC)
        mat.SetTransparency(transparency)
        ais_shape.SetMaterial(mat)
        ais_shape.SetDisplayMode(1)
        ais_shape.SetTransparency(transparency)
        self.context.Display(ais_shape, True)
        return ais_shape

    def display_wire(self, shape: TopoDS_Shape, color=(1.0, 1.0, 0.0), width=3.0):
        """Visualizza tutti i wire/edge di uno shape con colore e spessore."""
        if not self._is_initialized: self._init_ocp()
        ais_shape = AIS_Shape(shape)
        q_color = Quantity_Color(color[0], color[1], color[2], Quantity_TOC_RGB)
        ais_shape.SetColor(q_color)
        ais_shape.SetWidth(width)
        self.context.Display(ais_shape, True)
        return ais_shape

class AntCamViewerWindow(QMainWindow):
    def __init__(self, model, features: List, working_plane_normal=(0.0, 0.0, 1.0),
                 contour_shadow=None, perimeter=None, vertical_arc_groups=None):
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
        QTimer.singleShot(500, self._load_data)

    def _load_data(self):
        if not self.model or not self.model.brep: return
        
        bbox = Bnd_Box()
        BRepBndLib.Add_s(self.model.brep, bbox)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
        cx, cy, cz = (xmin+xmax)/2, (ymin+ymax)/2, (zmin+zmax)/2
        
        self.ocp_widget.display_shape(self.model.brep, transparency=0.7)
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
                    if not hole.props.get("from_arc_groups"):
                        continue
                    # Mostra sempre gli edge delle facce cilindriche
                    faces_to_show = hole.props.get("all_faces") or ([hole.geometry] if hole.geometry else [])
                    for f in faces_to_show:
                        self.ocp_widget.display_wire(f, color=color, width=1.0)
                    if show_hole_faces:
                        for f in faces_to_show:
                            self.ocp_widget.display_shadow(f, color=color, transparency=0.6)
                    # Mostra cap_faces semitrasparenti in verde chiaro
                    show_cap_faces = True  # flag per mostrare i candidati tappo
                    if show_cap_faces:
                        for cap in hole.props.get("cap_faces", []):
                            self.ocp_widget.display_shadow(cap, color=(0.2, 1.0, 0.2), transparency=0.4)
                continue
            if feat.geometry:
                if feat.props.get("through"):
                    wire = self._extract_zmin_wire(feat.geometry)
                    if wire:
                        self.ocp_widget.display_wire(wire, color=color, width=4.0)
                        continue
                self.ocp_widget.display_shape(feat.geometry, color=color)

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
                        self.ocp_widget.display_shadow(face, color=(0.0, 0.8, 1.0), transparency=0.5)
        # Visualizza perimetro ombra in arancio pieno
        for wire in self._perimeter:
            self.ocp_widget.display_wire(wire, color=(1.0, 0.5, 0.0), width=2.0)
        self.ocp_widget.view.Redraw()

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
            BRepBndLib.Add_s(self.model.brep, bbox)
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

def show_model_with_features(model, features, working_plane_normal=(0.0, 0.0, 1.0),
                             contour_shadow=None, perimeter=None, vertical_arc_groups=None):
    app = QApplication.instance() or QApplication(sys.argv)
    window = AntCamViewerWindow(model, features, working_plane_normal,
                                contour_shadow=contour_shadow, perimeter=perimeter,
                                vertical_arc_groups=vertical_arc_groups)
    window.show()
    sys.exit(app.exec())

