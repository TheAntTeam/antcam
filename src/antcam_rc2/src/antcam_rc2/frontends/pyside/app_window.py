"""Main application window: menus, toolbar, viewport and side panels."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QSplitter,
    QTabWidget,
    QToolBar,
    QWidget,
)

from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController
from antcam_rc2.frontends.pyside.dialogs.new_project_dialog import NewProjectDialog
from antcam_rc2.frontends.pyside.panels.geometries_panel import GeometriesPanel
from antcam_rc2.frontends.pyside.panels.operations_panel import OperationsPanel
from antcam_rc2.frontends.pyside.panels.parameters_panel import ParametersPanel
from antcam_rc2.frontends.pyside.panels.project_panel import ProjectPanel
from antcam_rc2.frontends.pyside.panels.simulation_panel import SimulationPanel
from antcam_rc2.frontends.pyside.panels.toolpath_panel import ToolpathPanel
from antcam_rc2.frontends.pyside.viewport.gl_viewport import GLViewport


class MainWindow(QMainWindow):
    """Assembles the full desktop UI around a :class:`ProjectController`."""

    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._project_path: Path | None = None
        self.setWindowTitle("AntCAM RC2")
        self.resize(1280, 860)

        self._viewport = GLViewport(self)
        self._project_panel = ProjectPanel(controller, self)
        self._geometries_panel = GeometriesPanel(controller, self)
        self._operations_panel = OperationsPanel(controller, self)
        self._parameters_panel = ParametersPanel(controller, self)
        self._toolpath_panel = ToolpathPanel(controller, self)
        self._simulation_panel = SimulationPanel(controller, self)

        tabs = QTabWidget(self)
        tabs.addTab(self._project_panel, "Project")
        tabs.addTab(self._geometries_panel, "Geometries")
        tabs.addTab(self._operations_panel, "Operations")
        tabs.addTab(self._parameters_panel, "Parameters")
        tabs.addTab(self._toolpath_panel, "Toolpath")
        tabs.addTab(self._simulation_panel, "Simulation")

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._viewport)
        splitter.addWidget(tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 380])
        self.setCentralWidget(splitter)

        self._status = QLabel("Ready", self)
        self.statusBar().addWidget(self._status)

        self._build_menus()
        self._build_toolbar()

        # Controller -> UI refresh wiring.
        controller.project_opened.connect(self._refresh_all)
        controller.project_closed.connect(self._refresh_all)
        controller.scene_changed.connect(self._on_scene_changed)
        controller.operations_changed.connect(self._refresh_operations)
        controller.fixtures_changed.connect(self._project_panel.refresh)
        controller.stock_changed.connect(self._on_stock_changed)
        controller.selection_changed.connect(self._on_selection_changed)
        controller.toolpath_changed.connect(self._on_toolpath_changed)
        controller.status_message.connect(self._status.setText)
        self._viewport.entity_picked.connect(self._on_entity_picked)
        self._viewport.solid_feature_picked.connect(self._on_solid_feature_picked)
        controller.bind_viewport(self._viewport)

    # ------------------------------------------------------------------ wiring
    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        new_action = QAction("&New project", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self._on_new_project)
        open_action = QAction("&Open project...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(lambda: self._controller.open_project(self._ask_open_path()))
        save_action = QAction("&Save project...", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._on_save_project)
        import_action = QAction("&Import geometry...", self)
        import_action.triggered.connect(self._on_import_geometry)
        import_solid_action = QAction("Import &solid...", self)
        import_solid_action.triggered.connect(self._on_import_solid)
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(new_action)
        file_menu.addAction(open_action)
        save_action = QAction("&Save project...", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._on_save_project)
        save_as_action = QAction("Save project &as...", self)
        save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        save_as_action.triggered.connect(self._on_save_project_as)
        file_menu.addAction(new_action)
        file_menu.addAction(open_action)
        file_menu.addAction(save_action)
        file_menu.addAction(save_as_action)
        file_menu.addSeparator()
        file_menu.addAction(import_action)
        file_menu.addAction(import_solid_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        undo_action = QAction("&Undo", self)
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        undo_action.triggered.connect(self._controller.undo)
        redo_action = QAction("&Redo", self)
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        redo_action.triggered.connect(self._controller.redo)
        select_action = QAction("&Select geometry", self)
        select_action.setCheckable(True)
        select_action.triggered.connect(self._viewport.set_picking_enabled)
        select_solid_action = QAction("Select 3D &feature", self)
        select_solid_action.setCheckable(True)
        select_solid_action.triggered.connect(self._viewport.set_solid_picking_enabled)
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(select_action)
        edit_menu.addAction(select_solid_action)

        view_menu = self.menuBar().addMenu("&View")
        fit_action = QAction("&Fit view", self)
        fit_action.setShortcut("F")
        fit_action.triggered.connect(self._viewport.fit_view)
        view_menu.addAction(fit_action)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        new_action = QAction("New", self)
        new_action.triggered.connect(self._on_new_project)
        open_action = QAction("Open", self)
        open_action.triggered.connect(lambda: self._controller.open_project(self._ask_open_path()))
        generate_action = QAction("Plan", self)
        generate_action.triggered.connect(self._controller.generate_toolpath)
        fit_action = QAction("Fit", self)
        fit_action.triggered.connect(self._viewport.fit_view)
        toolbar.addAction(new_action)
        toolbar.addAction(open_action)
        toolbar.addSeparator()
        toolbar.addAction(generate_action)
        toolbar.addAction(fit_action)
        self.addToolBar(toolbar)

    # ------------------------------------------------------------------ actions
    def _on_new_project(self) -> None:
        dialog = NewProjectDialog(self._controller.services, self)
        if dialog.exec():
            self._controller.new_project(dialog.name(), dialog.machine_id(), dialog.stock())
            self._project_path = None
            self.setWindowTitle("AntCAM RC2")

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "AntCAM project (*.antcam.json)")
        if path:
            self._controller.open_project(Path(path))
            self._project_path = Path(path)
            project = self._controller.project
            if project is not None:
                self.setWindowTitle(f"AntCAM RC2 — {project.name}")

    def _on_save_project(self) -> None:
        project = self._controller.project
        if project is None:
            return
        if self._project_path is not None:
            self._controller.save_project(self._project_path)
        else:
            self._on_save_project_as()

    def _on_save_project_as(self) -> None:
        project = self._controller.project
        if project is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project as...", f"{project.name}.antcam.json", "AntCAM project (*.antcam.json)"
        )
        if path:
            self._controller.save_project(Path(path))
            self._project_path = Path(path)
            self.setWindowTitle(f"AntCAM RC2 — {project.name}")

    def _on_import_geometry(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import geometry", "", "2D drawings (*.dxf *.svg);;All files (*)")
        if path:
            self._controller.import_geometry(Path(path))

    def _on_import_solid(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import solid", "", "3D solids (*.step *.stp *.stl);;All files (*)")
        if path:
            self._controller.import_solid(Path(path))

    def _ask_open_path(self) -> Path:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "AntCAM project (*.antcam.json)")
        return Path(path)

    # ------------------------------------------------------------------ refresh
    def _refresh_all(self) -> None:
        self._project_panel.refresh()
        self._geometries_panel.refresh()
        self._refresh_operations()
        self._parameters_panel.refresh()
        self._toolpath_panel.refresh()
        self._simulation_panel.refresh()

    def _refresh_operations(self) -> None:
        self._operations_panel.refresh()
        self._parameters_panel.refresh()

    def _on_scene_changed(self) -> None:
        self._viewport.set_render_scene(self._controller.render_scene())
        self._viewport.fit_view()

    def _on_selection_changed(self) -> None:
        self._parameters_panel.refresh()

    def _on_toolpath_changed(self) -> None:
        self._viewport.set_render_scene(self._controller.render_scene())
        self._toolpath_panel.refresh()

    def _on_stock_changed(self) -> None:
        """Recenter the view on the stock center when stock changes."""
        self._viewport.fit_to_stock(self._controller.project.stock if self._controller.project else None)

    def _on_solid_feature_picked(self, body_index: int, feature_index: int) -> None:
        self._controller.select_solid_feature(body_index, feature_index)

    def _on_entity_picked(self, picking_id: int) -> None:
        scene = self._controller.render_scene()
        entry = scene.pick_entry(picking_id)
        operation_id = self._controller.selected_operation_id
        if entry is None:
            return
        if (
            entry.kind == "geometry"
            and operation_id is not None
            and entry.layer is not None
            and entry.entity_index is not None
        ):
            self._controller.update_geometry_refs(operation_id, entry.layer, entry.entity_index)
        elif entry.kind == "operation" and entry.operation_id is not None:
            self._controller.select_operation(entry.operation_id)
