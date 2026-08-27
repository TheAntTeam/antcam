"""Geometries panel: manage imported 2D/3D geometries."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from antcam_rc2.core.rendering import geometry_to_scene, solid_to_scene
from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController


class GeometriesPanel(QWidget):
    """Manage imported 2D/3D geometries for the current project."""

    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

        layout = QVBoxLayout(self)

        # --- 2D Geometries section ---
        geom2d_group = QGroupBox("2D Geometries (DXF/SVG)")
        geom2d_layout = QVBoxLayout(geom2d_group)

        self._geom2d_list = QListWidget(self)
        self._geom2d_list.setToolTip("Imported 2D geometries")
        geom2d_layout.addWidget(self._geom2d_list)

        geom2d_buttons = QHBoxLayout()
        import_2d = QPushButton("Import 2D Geometry...", self)
        recenter_2d = QPushButton("Recenter on Stock", self)
        edit_2d = QPushButton("Edit Position...", self)
        remove_2d = QPushButton("Remove", self)
        remove_2d.setObjectName("danger")
        geom2d_buttons.addWidget(import_2d)
        geom2d_buttons.addWidget(recenter_2d)
        geom2d_buttons.addWidget(edit_2d)
        geom2d_buttons.addWidget(remove_2d)
        geom2d_layout.addLayout(geom2d_buttons)

        layout.addWidget(geom2d_group)

        # --- 3D Solids section ---
        geom3d_group = QGroupBox("3D Solids (STEP/STL)")
        geom3d_layout = QVBoxLayout(geom3d_group)

        self._geom3d_list = QListWidget(self)
        self._geom3d_list.setToolTip("Imported 3D solids")
        geom3d_layout.addWidget(self._geom3d_list)

        geom3d_buttons = QHBoxLayout()
        import_3d = QPushButton("Import 3D Solid...", self)
        recenter_3d = QPushButton("Recenter on Stock", self)
        edit_3d = QPushButton("Edit Position/Rotation...", self)
        remove_3d = QPushButton("Remove", self)
        remove_3d.setObjectName("danger")
        geom3d_buttons.addWidget(import_3d)
        geom3d_buttons.addWidget(recenter_3d)
        geom3d_buttons.addWidget(edit_3d)
        geom3d_buttons.addWidget(remove_3d)
        geom3d_layout.addLayout(geom3d_buttons)

        layout.addWidget(geom3d_group)

        # Connect signals
        import_2d.clicked.connect(self._on_import_2d)
        recenter_2d.clicked.connect(self._on_recenter_2d)
        edit_2d.clicked.connect(self._on_edit_2d)
        remove_2d.clicked.connect(self._on_remove_2d)

        import_3d.clicked.connect(self._on_import_3d)
        recenter_3d.clicked.connect(self._on_recenter_3d)
        edit_3d.clicked.connect(self._on_edit_3d)
        remove_3d.clicked.connect(self._on_remove_3d)

        # Refresh when scene changes (geometries loaded)
        self._controller.scene_changed.connect(self.refresh)

        # Connect to geometry import controller busy state
        self._controller.geometry_import_controller.busy_changed.connect(self._on_import_busy)
        self._import_2d_button = import_2d
        self._import_3d_button = import_3d

    def refresh(self) -> None:
        """Refresh all lists from the current project."""
        project = self._controller.project

        # 2D Geometries - show by layer/file
        self._geom2d_list.clear()
        if project and self._controller.scene:
            scene = self._controller.scene
            item_text = f"2D Geometry: {scene.source.path}" if scene.source.path else "2D Geometry (DXF/SVG)"
            item = QListWidgetItem(item_text)
            item.setData(0x0100, ("geometry", "all"))
            self._geom2d_list.addItem(item)

        # 3D Solids - show by file (not bodies/features)
        self._geom3d_list.clear()
        if project and self._controller._solid_scene:
            scene = self._controller._solid_scene
            item_text = f"3D Solid: {scene.source.path}" if scene.source.path else "3D Solid (STEP/STL)"
            item = QListWidgetItem(item_text)
            item.setData(0x0100, ("solid", "all"))
            self._geom3d_list.addItem(item)

    # ------------------------------------------------------------------ actions
    def _on_import_2d(self) -> None:
        if self._controller.project is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import 2D Geometry",
            "",
            "2D Drawings (*.dxf *.svg);;All Files (*)",
        )
        if path:
            self._controller.import_geometry(Path(path))
            self.refresh()

    def _on_import_3d(self) -> None:
        if self._controller.project is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import 3D Solid",
            "",
            "3D Solids (*.step *.stp *.stl);;All Files (*)",
        )
        if path:
            self._controller.import_solid(Path(path))
            self.refresh()

    def _on_recenter_2d(self) -> None:
        if self._controller.project is None or self._controller.scene is None:
            return
        self._controller._center_geometry_in_stock(self._controller.scene)
        self._controller._rebuild_setup_graph()
        self._controller.scene_changed.emit()
        self.refresh()

    def _on_recenter_3d(self) -> None:
        if self._controller.project is None or self._controller._solid_scene is None:
            return
        # Recenter 3D solid using the same origin as 2D (center_xy_top_z)
        scene = self._controller._solid_scene
        stock = self._controller.project.stock
        wcs = self._controller.project.wcs
        target_x = stock.position_x_mm + wcs.offset_x_mm + stock.width_mm / 2.0
        target_y = stock.position_y_mm + wcs.offset_y_mm + stock.length_mm / 2.0
        bbox = scene.bounding_box()
        self._controller._solid_scene = scene.translate(
            target_x - bbox.center[0],
            target_y - bbox.center[1],
            -bbox.center[2],
        )
        self._controller._rebuild_setup_graph()
        self._controller.scene_changed.emit()
        self.refresh()

    def _on_edit_2d(self) -> None:
        """Edit position of selected 2D geometry layer."""
        item = self._geom2d_list.currentItem()
        if not item:
            return
        data = item.data(0x0100)
        if not data or data[0] != "geometry":
            return
        layer_name = data[1]
        # TODO: Open dialog to edit position/rotation of 2D layer
        self._controller.status_message.emit(
            f"Edit 2D geometry position for layer '{layer_name}' - not yet implemented"
        )

    def _on_remove_2d(self) -> None:
        """Remove selected 2D geometry layer."""
        item = self._geom2d_list.currentItem()
        if not item:
            return
        data = item.data(0x0100)
        if not data or data[0] != "geometry":
            return
        layer_name = data[1]
        if self._controller.project is None or self._controller.scene is None:
            return
        scene = self._controller.scene
        # Remove the layer from the scene
        new_layers = [layer for layer in scene.layers if layer.name != layer_name]
        # Create new scene with filtered layers
        from antcam_rc2.core.io.diagnostics import ImportDiagnostics
        from antcam_rc2.core.io.scene import GeometryScene, SourceInfo

        new_scene = GeometryScene(
            source=SourceInfo(format="dxf", path=""),
            units=scene.units,
            layers=new_layers,
            diagnostics=ImportDiagnostics(),
            tolerance_mm=scene.tolerance_mm,
        )
        self._controller._scene = new_scene
        self._controller._geometry_graph = geometry_to_scene(new_scene)
        self._controller._rebuild_setup_graph()
        self._controller.scene_changed.emit()
        # Clear toolpaths since geometry changed
        self._controller.clear_toolpaths()
        self._controller.status_message.emit(f"Removed 2D geometry layer '{layer_name}'")
        self.refresh()

    def _on_edit_3d(self) -> None:
        """Edit position/rotation of selected 3D solid body."""
        item = self._geom3d_list.currentItem()
        if not item:
            return
        data = item.data(0x0100)
        if not data or data[0] != "solid":
            return
        body_idx = data[1]
        # TODO: Open dialog to edit position/rotation of 3D body
        self._controller.status_message.emit(
            f"Edit 3D solid position/rotation for body {body_idx} - not yet implemented"
        )

    def _on_remove_3d(self) -> None:
        """Remove selected 3D solid body."""
        item = self._geom3d_list.currentItem()
        if not item:
            return
        data = item.data(0x0100)
        if not data or data[0] != "solid":
            return
        body_idx = data[1]
        if self._controller.project is None or self._controller._solid_scene is None:
            return
        scene = self._controller._solid_scene
        new_bodies = [b for i, b in enumerate(scene.bodies) if i != body_idx]
        from antcam_rc2.core.geometry3d.scene import SolidScene

        new_scene = SolidScene(
            source=scene.source,
            bodies=tuple(new_bodies),
            units=scene.units,
            tolerance_mm=scene.tolerance_mm,
            warnings=scene.warnings,
            errors=scene.errors,
        )
        self._controller._solid_scene = new_scene
        self._controller._solid_graph = solid_to_scene(new_scene)
        self._controller._rebuild_setup_graph()
        self._controller.scene_changed.emit()
        # Clear toolpaths since geometry changed
        self._controller.clear_toolpaths()
        self.refresh()

    def _on_import_busy(self, busy: bool) -> None:
        """Update button states when import is in progress."""
        self._import_2d_button.setEnabled(not busy)
        self._import_3d_button.setEnabled(not busy)
        if busy:
            self._import_2d_button.setText("Importing...")
            self._import_3d_button.setText("Importing...")
        else:
            self._import_2d_button.setText("Import 2D Geometry...")
            self._import_3d_button.setText("Import 3D Solid...")
