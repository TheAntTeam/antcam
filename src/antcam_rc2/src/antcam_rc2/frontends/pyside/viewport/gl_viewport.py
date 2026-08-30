"""The OpenGL viewport widget: input handling, camera and picking."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from antcam_rc2.core.geometry3d.picking import ray_mesh_hit
from antcam_rc2.core.geometry3d.scene import SolidScene
from antcam_rc2.core.project.models import Stock
from antcam_rc2.core.rendering.scene_graph import RenderScene
from antcam_rc2.frontends.pyside.viewport.camera import OrbitCamera
from antcam_rc2.frontends.pyside.viewport.gl_log import gl_log
from antcam_rc2.frontends.pyside.viewport.lights import Lights
from antcam_rc2.frontends.pyside.viewport.renderer import Renderer


class GLViewport(QOpenGLWidget):
    """Interactive 3D viewport rendering a :class:`RenderScene`."""

    entity_picked = Signal(int)  # picking id (0 = none)
    solid_feature_picked = Signal(int, int)  # (body_index, feature_index)
    view_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._renderer = Renderer()
        self._renderer_ready = False
        self._gl_failed = False
        self._camera = OrbitCamera()
        self._lights = Lights()
        self._scene = RenderScene()
        self._scene_box = None
        self._picking_enabled = False
        self._solid_picking_enabled = False
        self._solid_scene: SolidScene | None = None
        self._last_mouse = None
        self._dragging = False

    # ------------------------------------------------------------------ scene
    def set_render_scene(self, scene: RenderScene) -> None:
        """Store a new render graph; uploads when the GL context is ready.

        The graph is kept even before ``initializeGL`` runs (e.g. during
        window assembly or under the offscreen test platform), so the first
        realized frame always shows the latest scene.
        """
        self._scene = scene
        box = scene.bounding_box()
        self._scene_box = None if box.is_empty else box
        gl_log(
            f"set_render_scene: nodes={len(scene.nodes)} box_empty={box.is_empty} renderer_ready={self._renderer_ready}"
        )
        if self._renderer_ready:
            self._renderer.set_scene(scene, self._scene_box)
        self.update()

    def set_voxel_mesh(self, vertices, triangles, color) -> None:
        """Show the simulated material surface (or pass empty arrays to clear)."""
        if self._renderer_ready:
            self._renderer.set_voxel_mesh(vertices, triangles, color)
            self.update()

    def set_tool_marker(self, position: tuple[float, float, float] | None) -> None:
        """Show/hide the tool position marker during simulation."""
        if self._renderer_ready:
            self._renderer.set_tool_marker(position)
            self.update()

    def set_picking_enabled(self, enabled: bool) -> None:
        """Enable click-to-pick mode (geometry selection)."""
        self._picking_enabled = enabled
        if enabled:
            self._solid_picking_enabled = False
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def set_solid_scene(self, scene: SolidScene | None) -> None:
        """Store the 3D solid scene used by CPU ray picking."""
        self._solid_scene = scene

    def set_solid_picking_enabled(self, enabled: bool) -> None:
        """Enable click-to-pick mode for 3D features."""
        self._solid_picking_enabled = enabled
        if enabled:
            self._picking_enabled = False
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def fit_view(self) -> None:
        """Frame the current scene bounding box."""
        if self._scene_box is not None:
            self._camera.fit_to(self._scene_box, (self.width(), self.height()))
            self.update()
            self.view_changed.emit()

    def fit_to_stock(self, stock: Stock | None, wcs=None) -> None:
        """Frame the stock bounding box (WCS-aware, respects StockOrigin)."""
        if stock is None:
            return
        from antcam_rc2.core.rendering.builder import RenderBox

        ox = 0.0 if wcs is None else float(wcs.offset_x_mm)
        oy = 0.0 if wcs is None else float(wcs.offset_y_mm)
        oz = 0.0 if wcs is None else float(wcs.offset_z_mm)
        px = float(stock.position_x_mm) + ox
        py = float(stock.position_y_mm) + oy
        pz = float(stock.position_z_mm) + oz
        w = float(stock.width_mm)
        length = float(stock.length_mm)
        h = float(stock.height_mm)
        origin = stock.origin.value
        if origin == "center_xy_top_z":
            min_x, max_x = px - w / 2.0, px + w / 2.0
            min_y, max_y = py - length / 2.0, py + length / 2.0
            min_z, max_z = pz - h, pz
        elif origin == "corner_xy_top_z":
            min_x, max_x = px, px + w
            min_y, max_y = py, py + length
            min_z, max_z = pz - h, pz
        elif origin == "center_xy_zero_z":
            min_x, max_x = px - w / 2.0, px + w / 2.0
            min_y, max_y = py - length / 2.0, py + length / 2.0
            min_z, max_z = pz, pz + h
        else:  # corner_xy_zero_z
            min_x, max_x = px, px + w
            min_y, max_y = py, py + length
            min_z, max_z = pz, pz + h
        box = RenderBox(min_x=min_x, min_y=min_y, min_z=min_z, max_x=max_x, max_y=max_y, max_z=max_z)
        self._camera.fit_to(box, (self.width(), self.height()))
        self.update()
        self.view_changed.emit()

    # ------------------------------------------------------------------ GL hooks
    def initializeGL(self) -> None:
        gl_log("initializeGL called")
        try:
            self._renderer.initialize()
            self._renderer_ready = True
            if self._scene.nodes:
                self._renderer.set_scene(self._scene, self._scene_box)
        except Exception as exc:  # noqa: BLE001 - never let a GL failure kill the app
            gl_log("initializeGL FAILED:", type(exc).__name__, str(exc)[:400])
            self._gl_failed = True

    def resizeGL(self, width: int, height: int) -> None:
        if self._renderer_ready:
            try:
                self._renderer.resize(width, height)
            except Exception as exc:  # noqa: BLE001
                gl_log("resizeGL FAILED:", type(exc).__name__, str(exc)[:300])
                self._gl_failed = True

    def paintGL(self) -> None:
        gl_log("paintGL called")
        if self._renderer_ready and not self._gl_failed:
            try:
                self._renderer.paint(self._camera, self._lights, self._scene_box)
            except Exception as exc:  # noqa: BLE001
                gl_log("paintGL FAILED:", type(exc).__name__, str(exc)[:300])
                self._gl_failed = True

    # ------------------------------------------------------------------ input
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._solid_picking_enabled:
                self._pick_solid_feature(event)
                return
            if self._picking_enabled and self._renderer_ready:
                picking_id = self._renderer.pick(self._camera, int(event.position().x()), int(event.position().y()))
                self.entity_picked.emit(picking_id)
                return
        # Start dragging for left, middle, or right button
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._last_mouse = event.position()
            self._dragging = True

    def _pick_solid_feature(self, event: QMouseEvent) -> None:
        if self._solid_scene is None:
            return
        ray = self._camera.screen_ray((event.position().x(), event.position().y()), (self.width(), self.height()))
        hit = ray_mesh_hit(
            self._solid_scene,
            (float(ray.origin[0]), float(ray.origin[1]), float(ray.origin[2])),
            (float(ray.direction[0]), float(ray.direction[1]), float(ray.direction[2])),
        )
        if hit is not None and hit[1] >= 0:
            self.solid_feature_picked.emit(hit[0], hit[1])

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._dragging or self._last_mouse is None:
            return
        delta = event.position() - self._last_mouse
        self._last_mouse = event.position()
        if event.buttons() & Qt.MouseButton.LeftButton:
            # Nothing to do for left button
            pass
        elif event.buttons() & Qt.MouseButton.MiddleButton:
            # Pan: scene follows mouse
            self._camera.pan_pixels(delta.x(), delta.y(), (self.width(), self.height()))
        elif event.buttons() & Qt.MouseButton.RightButton:
            # Orbit with right button
            self._camera.orbit(-delta.x() * 0.3, delta.y() * 0.3)
        self.update()
        self.view_changed.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._dragging = False
            self._last_mouse = None

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 0.9 if event.angleDelta().y() > 0 else 1.1
        self._camera.zoom(factor)
        self.update()
        self.view_changed.emit()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.fit_view()
