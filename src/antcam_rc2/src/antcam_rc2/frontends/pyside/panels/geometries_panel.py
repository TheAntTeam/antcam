"""Geometries panel: manage imported 2D/3D geometries."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from antcam_rc2.core.rendering import geometry_to_scene
from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController


class GeometriesPanel(QWidget):
    """Manage imported 2D/3D geometries for the current project."""

    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._updating_3d_selection = False

        layout = QVBoxLayout(self)

        # --- 2D Geometries section ---
        geom2d_group = QGroupBox("2D Geometries (DXF/SVG)")
        geom2d_layout = QVBoxLayout(geom2d_group)

        self._geom2d_name = QLineEdit(self)
        self._geom2d_name.setReadOnly(True)
        self._geom2d_name.setPlaceholderText("Nessuna geometria 2D")
        self._geom2d_name.setToolTip("Nome file della geometria 2D importata")
        geom2d_layout.addWidget(self._geom2d_name)

        self._geom2d_list = QListWidget(self)
        self._geom2d_list.setToolTip("Layer della geometria 2D")
        geom2d_layout.addWidget(self._geom2d_list)

        geom2d_buttons = QHBoxLayout()
        import_2d = QPushButton("Import 2D Geometry...", self)
        edit_2d = QPushButton("Edit Position...", self)
        remove_2d = QPushButton("Remove", self)
        remove_2d.setObjectName("danger")
        geom2d_buttons.addWidget(import_2d)
        geom2d_buttons.addWidget(edit_2d)
        geom2d_buttons.addWidget(remove_2d)
        geom2d_layout.addLayout(geom2d_buttons)

        layout.addWidget(geom2d_group)

        # --- 3D Solids section ---
        geom3d_group = QGroupBox("3D Solids (STEP/STL)")
        geom3d_layout = QVBoxLayout(geom3d_group)

        self._solid_name = QLineEdit(self)
        self._solid_name.setReadOnly(True)
        self._solid_name.setPlaceholderText("Nessun solido 3D")
        self._solid_name.setToolTip("Nome file del solido 3D importato")
        geom3d_layout.addWidget(self._solid_name)

        self._geom3d_list = QListWidget(self)
        self._geom3d_list.setToolTip("Feature del solido 3D (seleziona per evidenziare)")
        self._geom3d_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        geom3d_layout.addWidget(self._geom3d_list)

        geom3d_buttons = QHBoxLayout()
        import_3d = QPushButton("Import 3D Solid...", self)
        edit_3d = QPushButton("Edit Position/Rotation...", self)
        remove_3d = QPushButton("Remove", self)
        remove_3d.setObjectName("danger")
        geom3d_buttons.addWidget(import_3d)
        geom3d_buttons.addWidget(edit_3d)
        geom3d_buttons.addWidget(remove_3d)
        geom3d_layout.addLayout(geom3d_buttons)

        layout.addWidget(geom3d_group)

        # Connect signals
        import_2d.clicked.connect(self._on_import_2d)
        edit_2d.clicked.connect(self._on_edit_2d)
        remove_2d.clicked.connect(self._on_remove_2d)

        import_3d.clicked.connect(self._on_import_3d)
        edit_3d.clicked.connect(self._on_edit_3d)
        remove_3d.clicked.connect(self._on_remove_3d)
        self._geom3d_list.itemSelectionChanged.connect(self._on_3d_selection_changed)

        # Refresh when scene changes (geometries loaded)
        self._controller.scene_changed.connect(self.refresh)

        # Connect to geometry import controller busy state
        self._controller.geometry_import_controller.busy_changed.connect(self._on_import_busy)
        self._import_2d_button = import_2d
        self._import_3d_button = import_3d

    def refresh(self) -> None:
        """Refresh all lists from the current project."""
        project = self._controller.project

        # 2D Geometries - name field + layer list
        self._geom2d_list.clear()
        if project and self._controller.scene:
            scene = self._controller.scene
            name = scene.source.path if scene.source.path else "2D Geometry (DXF/SVG)"
            self._geom2d_name.setText(name)
            self._geom2d_name.setToolTip(name)
            # Show layers as individual items for clarity
            for layer in scene.layers:
                item_text = f"Layer '{layer.name}' ({len(layer.entities)} entità)"
                item = QListWidgetItem(item_text)
                item.setData(0x0100, ("geometry", layer.name))
                self._geom2d_list.addItem(item)
            if not scene.layers:
                item = QListWidgetItem("2D Geometry: (nessun layer)")
                item.setData(0x0100, ("geometry", "all"))
                self._geom2d_list.addItem(item)
        else:
            self._geom2d_name.clear()

        # 3D Solids - name field + feature list
        # Block signal to avoid feedback loop when syncing selection
        self._updating_3d_selection = True
        try:
            self._geom3d_list.clear()
            if project and self._controller._solid_scene:
                scene = self._controller._solid_scene
                name = scene.source.path if scene.source.path else "3D Solid (STEP/STL)"
                # Prefer body name if single body
                if len(scene.bodies) == 1 and scene.bodies[0].name:
                    # Keep path as tooltip, but name field shows path; body name added in list header
                    pass
                self._solid_name.setText(name)
                self._solid_name.setToolTip(name)
                for body_index, body in enumerate(scene.bodies):
                    for feature in body.features:
                        kind = feature.kind.value
                        z = feature.plane_z_mm
                        detail = ""
                        if feature.kind.value == "hole" and feature.radius is not None:
                            detail = f" r={feature.radius:.2f}mm"
                            if feature.center is not None:
                                detail += f" @({feature.center[0]:.1f},{feature.center[1]:.1f})"
                        elif feature.boundary:
                            detail = f" verts={len(feature.boundary)}"
                        facing = " ↑" if feature.facing else ""
                        item_text = f"{kind} [{body_index}:{feature.feature_index}] z={z:.2f}{detail}{facing}"
                        item = QListWidgetItem(item_text)
                        item.setData(0x0100, (body_index, feature.feature_index))
                        tooltip = f"Body {body_index} ({body.name}) — {kind} z={z:.2f}{detail}"
                        item.setToolTip(tooltip)
                        self._geom3d_list.addItem(item)
                        # Restore selection from controller
                        if (body_index, feature.feature_index) in self._controller.selected_solid_features:
                            item.setSelected(True)
                if scene.feature_count() == 0:
                    item = QListWidgetItem("(nessuna feature rilevata)")
                    item.setFlags(item.flags() & ~item.flags().__class__.ItemIsSelectable)  # type: ignore[attr-defined]
                    self._geom3d_list.addItem(item)
            else:
                self._solid_name.clear()
        finally:
            self._updating_3d_selection = False

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

    def _on_3d_selection_changed(self) -> None:
        if self._updating_3d_selection:
            return
        selected: set[tuple[int, int]] = set()
        for item in self._geom3d_list.selectedItems():
            data = item.data(0x0100)
            if isinstance(data, tuple) and len(data) == 2:
                selected.add((int(data[0]), int(data[1])))
        self._controller.set_selected_solid_features(selected)

    def _on_edit_2d(self) -> None:
        """Edit position of selected 2D geometry layer."""
        item = self._geom2d_list.currentItem()
        if not item:
            return
        data = item.data(0x0100)
        if not data or data[0] != "geometry":
            return
        layer_name = data[1]
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
        """Edit placement — delegates to the controller's shared modeless dialog (same as on import)."""
        if self._controller.project is None or self._controller._solid_scene is None:
            return
        # Same dialog as import, with live preview and non-modal viewport navigation.
        self._controller.edit_solid_placement()
        # Refresh is handled via scene_changed once the dialog closes; also refresh now for selection.
        self.refresh()

    def _on_remove_3d(self) -> None:
        """Remove the attached 3D solid (via persistent binding + refs + toolpaths)."""
        if self._controller.project is None:
            return
        if self._controller._solid_scene is None:
            self._controller.status_message.emit("Nessun solido 3D da rimuovere")
            return
        self._controller.remove_solid_completely()
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
