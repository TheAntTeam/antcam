"""Feature tree panel: unified view of stock, features (2D/3D) and their operations."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from antcam_rc2.core.geometry3d.scene import FeatureKind
from antcam_rc2.core.operations.registry import build_standard_registry
from antcam_rc2.core.project.models import OperationType
from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController
from antcam_rc2.frontends.pyside.dialogs.add_operation_dialog import AddOperationDialog
from antcam_rc2.frontends.pyside.panels.parameters_panel import ParametersPanel
from antcam_rc2.frontends.pyside.widgets.feature_tree_model import FeatureNodeKind, FeatureTreeModel


class FeatureTreePanel(QWidget):
    """Unified feature tree replacing Operations + Geometries panels."""

    # Feature kind -> compatible operation types mapping
    FEATURE_TO_OPERATIONS = {
        FeatureKind.HOLE: [
            OperationType.DRILL,
            OperationType.BORING,
            OperationType.HOLE_POCKETING,
            OperationType.THREAD_MILLING,
            OperationType.TAPPING,
        ],
        FeatureKind.FACE_PLANAR: [
            OperationType.FACING,
            OperationType.POCKETING,
            OperationType.ROUGHING,
            OperationType.FACE_TOP,
        ],
        FeatureKind.PERIMETER: [
            OperationType.PROFILING,
            OperationType.SLOTTING,
        ],
    }

    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._model = FeatureTreeModel(self)
        self._tree = QTreeView(self)
        self._tree.setModel(self._model)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setAnimated(True)
        self._tree.setExpandsOnDoubleClick(True)
        self._tree.selectionModel().currentChanged.connect(self._on_selection_changed)

        left = QWidget(self)
        layout = QVBoxLayout(left)

        # Toolbar
        toolbar = QHBoxLayout()
        self._import_2d_btn = QPushButton("Import 2D…", self)
        self._import_3d_btn = QPushButton("Import 3D…", self)
        self._remove_geo_btn = QPushButton("Remove Geometry", self)
        self._remove_geo_btn.setObjectName("danger")
        toolbar.addWidget(self._import_2d_btn)
        toolbar.addWidget(self._import_3d_btn)
        toolbar.addWidget(self._remove_geo_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        # Geometry/Source info
        self._source_name = QLineEdit(self)
        self._source_name.setReadOnly(True)
        self._source_name.setPlaceholderText("No geometry imported")
        layout.addWidget(self._source_name)

        # Tree view
        layout.addWidget(self._tree)

        # Action buttons
        buttons = QHBoxLayout()
        self._add_op_btn = QPushButton("Add Operation", self)
        self._add_op_btn.setObjectName("primary")
        self._duplicate_btn = QPushButton("Duplicate", self)
        self._remove_btn = QPushButton("Remove", self)
        self._remove_btn.setObjectName("danger")
        self._up_btn = QPushButton("Up", self)
        self._down_btn = QPushButton("Down", self)
        buttons.addWidget(self._add_op_btn)
        buttons.addWidget(self._duplicate_btn)
        buttons.addWidget(self._remove_btn)
        buttons.addStretch(1)
        buttons.addWidget(self._up_btn)
        buttons.addWidget(self._down_btn)
        layout.addLayout(buttons)

        # Parameters for the selected operation live next to the tree, so the
        # link between an operation and its parameters is obvious (the old
        # standalone "Parameters" tab looked disconnected from the operations).
        self._parameters_panel = ParametersPanel(controller, self)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(self._parameters_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([640, 420])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        # Stability: remember the inputs so redundant refreshes are skipped and
        # the current selection survives the refreshes that do rebuild the tree.
        self._last_project = None
        self._last_solid_scene = None
        self._last_scene = None
        self._restoring = False

        # Connect signals
        self._import_2d_btn.clicked.connect(self._on_import_2d)
        self._import_3d_btn.clicked.connect(self._on_import_3d)
        self._remove_geo_btn.clicked.connect(self._on_remove_geometry)
        self._add_op_btn.clicked.connect(self._on_add_operation)
        self._duplicate_btn.clicked.connect(self._on_duplicate)
        self._remove_btn.clicked.connect(self._on_remove)
        self._up_btn.clicked.connect(lambda: self._move_selected(-1))
        self._down_btn.clicked.connect(lambda: self._move_selected(1))

        # Controller signals
        self._controller.scene_changed.connect(self.refresh)
        self._controller.operations_changed.connect(self.refresh)
        self._controller.project_opened.connect(self.refresh)
        self._controller.project_closed.connect(self.refresh)
        self._controller.fixtures_changed.connect(self.refresh)
        self._controller.stock_changed.connect(self.refresh)
        self._controller.selection_changed.connect(self._parameters_panel.refresh)
        self._controller.operations_changed.connect(self._parameters_panel.refresh)
        self._controller.project_opened.connect(self._parameters_panel.refresh)
        self._controller.project_closed.connect(self._parameters_panel.refresh)
        self._parameters_panel.refresh()  # start disabled until an operation is selected

    def refresh(self) -> None:
        """Rebuild the tree when its inputs changed, keeping the selection.

        ``scene_changed`` fires on every selection highlight, so rebuilding
        unconditionally used to reset the tree (and lose the selected
        operation) several times per click.  We rebuild only when the project
        or one of the scenes actually changed.
        """
        project = self._controller.project
        solid_scene = self._controller._solid_scene
        scene = self._controller.scene

        if project is self._last_project and solid_scene is self._last_solid_scene and scene is self._last_scene:
            return
        self._last_project = project
        self._last_solid_scene = solid_scene
        self._last_scene = scene

        self._restoring = True
        try:
            self._model.set_data(project, solid_scene, scene)
            self._tree.expand(self._model.index(0, 0, QModelIndex()))
            self._restore_selection()
        finally:
            self._restoring = False

        self._update_source_name(solid_scene, scene)

    def _restore_selection(self) -> None:
        """Re-select the controller's active operation/feature after a rebuild."""
        index = QModelIndex()
        operation_id = self._controller.selected_operation_id
        if operation_id is not None:
            index = self._model.index_for_operation(operation_id)
        if not index.isValid():
            selected = self._controller.selected_solid_features
            if selected:
                body_index, feature_index = next(iter(selected))
                index = self._model.index_for_feature(body_index=body_index, feature_index=feature_index)
        if index.isValid():
            parent = index.parent()
            if parent.isValid():
                self._tree.expand(parent)
            self._tree.setCurrentIndex(index)
            self._tree.scrollTo(index)

    def _update_source_name(self, solid_scene, scene) -> None:
        if solid_scene is not None:
            from antcam_rc2.core.geometry3d.scene import SolidScene

            if isinstance(solid_scene, SolidScene):
                path = solid_scene.source.path
                self._source_name.setText(path if path else "3D Solid")
                return
        if scene is not None:
            from antcam_rc2.core.io.scene import GeometryScene

            if isinstance(scene, GeometryScene):
                path = scene.source.path
                self._source_name.setText(path if path else "2D Geometry")
                return
        self._source_name.clear()

    def _on_selection_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if self._restoring:
            return
        if not current.isValid():
            self._controller.select_operation(None)
            return

        node = current.internalPointer()
        from antcam_rc2.frontends.pyside.widgets.feature_tree_model import FeatureTreeNode

        if not isinstance(node, FeatureTreeNode):
            return

        if node.kind == FeatureNodeKind.OPERATION and node.operation_id:
            self._controller.select_operation(node.operation_id)
        elif node.kind == FeatureNodeKind.FEATURE:
            # Clear the operation selection FIRST: selecting features while an
            # operation is active re-targets that operation's references, which
            # silently moved an operation from its feature to the clicked one.
            # Clicking a feature here only highlights it.
            self._controller.select_operation(None)
            if node.feature is not None:
                self._controller.set_selected_solid_features({(node.body_index, node.feature_index)})

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
            self._controller.import_geometry(path)

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
            self._controller.import_solid(path)

    def _on_remove_geometry(self) -> None:
        if self._controller.project is None:
            return
        if self._controller._solid_scene is not None:
            self._controller.remove_solid_completely()
        elif self._controller.scene is not None:
            self._controller.remove_geometry_completely()

    def _on_add_operation(self) -> None:
        """Add operation for the currently selected feature or at root level."""
        index = self._tree.currentIndex()
        if not index.isValid():
            # No selection - use generic dialog
            self._show_add_operation_dialog()
            return

        node = index.internalPointer()
        from antcam_rc2.frontends.pyside.widgets.feature_tree_model import FeatureTreeNode

        if not isinstance(node, FeatureTreeNode):
            self._show_add_operation_dialog()
            return

        if node.kind == FeatureNodeKind.FEATURE:
            # Feature selected - suggest operations for this feature type
            self._show_feature_operations_dialog(node)
        elif node.kind == FeatureNodeKind.OPERATION:
            # Operation selected - add after it
            self._show_add_operation_dialog()
        else:
            # Stock or other - generic
            self._show_add_operation_dialog()

    def _show_feature_operations_dialog(self, feature_node) -> None:
        """Show dialog with operations filtered for the selected feature type."""
        if feature_node.feature is None:
            self._show_add_operation_dialog()
            return

        feature_kind = feature_node.feature.kind
        allowed_types = self.FEATURE_TO_OPERATIONS.get(feature_kind, [])

        if not allowed_types:
            self._show_add_operation_dialog()
            return

        dialog = FeatureOperationDialog(
            self._controller.services,
            allowed_types,
            feature_node,
            self,
        )
        if dialog.exec():
            operation_id = self._controller.add_operation(
                dialog.operation_type(),
                tool_id=dialog.tool_id(),
                cooling_id=dialog.cooling_id(),
                name=dialog.name(),
            )
            if operation_id is not None:
                # Bind this operation to the feature
                if feature_node.feature is not None:
                    self._controller.replace_solid_refs_for_operation(
                        operation_id, [(feature_node.body_index, feature_node.feature_index)]
                    )
                self._controller.select_operation(operation_id)

    def _show_add_operation_dialog(self) -> None:
        if self._controller.project is None:
            return
        dialog = AddOperationDialog(self._controller.services, self)
        if dialog.exec():
            operation_id = self._controller.add_operation(
                dialog.operation_type(),
                tool_id=dialog.tool_id(),
                cooling_id=dialog.cooling_id(),
                name=dialog.name(),
            )
            if operation_id is not None:
                self._controller.select_operation(operation_id)

    def _on_duplicate(self) -> None:
        index = self._tree.currentIndex()
        if not index.isValid():
            return
        node = index.internalPointer()
        from antcam_rc2.frontends.pyside.widgets.feature_tree_model import FeatureTreeNode

        if isinstance(node, FeatureTreeNode) and node.kind == FeatureNodeKind.OPERATION and node.operation_id:
            self._controller.duplicate_operation(node.operation_id)

    def _on_remove(self) -> None:
        index = self._tree.currentIndex()
        if not index.isValid():
            return
        node = index.internalPointer()
        from antcam_rc2.frontends.pyside.widgets.feature_tree_model import FeatureTreeNode

        if isinstance(node, FeatureTreeNode) and node.kind == FeatureNodeKind.OPERATION and node.operation_id:
            self._controller.remove_operation(node.operation_id)

    def _move_selected(self, delta: int) -> None:
        index = self._tree.currentIndex()
        if not index.isValid():
            return
        node = index.internalPointer()
        from antcam_rc2.frontends.pyside.widgets.feature_tree_model import FeatureTreeNode

        if isinstance(node, FeatureTreeNode) and node.kind == FeatureNodeKind.OPERATION and node.operation_id:
            self._controller.move_operation_by(node.operation_id, delta)

    def _on_context_menu(self, position) -> None:
        index = self._tree.indexAt(position)
        if not index.isValid():
            return

        node = index.internalPointer()
        from antcam_rc2.frontends.pyside.widgets.feature_tree_model import FeatureTreeNode

        if not isinstance(node, FeatureTreeNode):
            return

        menu = QMenu(self)

        if node.kind == FeatureNodeKind.FEATURE:
            add_action = menu.addAction("Add operation for this feature…")
            menu.addSeparator()
            select_action = menu.addAction("Select in viewport")
            action = menu.exec(self._tree.mapToGlobal(position))
            if action is None:
                return
            if action is add_action:
                self._show_feature_operations_dialog(node)
            elif action is select_action:
                if node.feature is not None:
                    self._controller.set_selected_solid_features({(node.body_index, node.feature_index)})

        elif node.kind == FeatureNodeKind.OPERATION:
            add_action = menu.addAction("Add operation…")
            menu.addSeparator()
            duplicate_action = menu.addAction("Duplicate")
            copy_action = menu.addAction("Copy")
            remove_action = menu.addAction("Remove")
            menu.addSeparator()
            toggle_action = menu.addAction("Toggle enabled")
            up_action = menu.addAction("Move up")
            down_action = menu.addAction("Move down")
            action = menu.exec(self._tree.mapToGlobal(position))
            if action is None or node.operation_id is None:
                return
            if action is add_action:
                self._show_add_operation_dialog()
            elif action is duplicate_action:
                self._controller.duplicate_operation(node.operation_id)
            elif action is copy_action:
                from antcam_rc2.frontends.pyside.settings.clipboard import operation_clipboard

                operation = next(
                    (op for op in self._controller.project.operations if op.id == node.operation_id),
                    None,
                )
                if operation is not None:
                    operation_clipboard.copy(operation)
            elif action is remove_action:
                self._controller.remove_operation(node.operation_id)
            elif action is toggle_action:
                operation = next(
                    (op for op in self._controller.project.operations if op.id == node.operation_id),
                    None,
                )
                if operation is not None:
                    self._controller.toggle_operation(node.operation_id, not operation.enabled)
            elif action is up_action:
                self._move_selected(-1)
            elif action is down_action:
                self._move_selected(1)


class FeatureOperationDialog:
    """Dialog for adding an operation pre-filtered for a specific feature type."""

    def __init__(self, services, allowed_types: list[OperationType], feature_node, parent=None) -> None:
        from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QVBoxLayout

        self._dialog = QDialog(parent)
        self._dialog.setWindowTitle("Add operation for feature")
        self._services = services
        self._registry = build_standard_registry()
        self._bundle = services.catalog_repository.load()
        self._allowed_types = allowed_types
        self._feature_node = feature_node

        layout = QVBoxLayout(self._dialog)
        form = QFormLayout()

        self._type = QComboBox(self._dialog)
        for definition in self._registry.definitions():
            if definition.operation_type in self._allowed_types:
                self._type.addItem(definition.display_name, definition.operation_type.value)

        self._tool = QComboBox(self._dialog)
        self._cooling = QComboBox(self._dialog)
        for cooling_id in sorted(self._bundle.cooling):
            self._cooling.addItem(self._bundle.cooling[cooling_id].name, cooling_id)

        self._name = QLineEdit(self._dialog)
        form.addRow("Type", self._type)
        form.addRow("Tool", self._tool)
        form.addRow("Cooling", self._cooling)
        form.addRow("Name", self._name)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self._dialog,
        )
        buttons.accepted.connect(self._dialog.accept)
        buttons.rejected.connect(self._dialog.reject)
        layout.addWidget(buttons)

        self._type.currentIndexChanged.connect(self._reload_tools)
        self._reload_tools()

    def _reload_tools(self) -> None:
        op_type = self._type.currentData()
        definition = self._registry.definition(op_type)
        self._tool.clear()
        # Use FULL tool library, not just allowed_tool_types
        for tool_id in sorted(self._bundle.tools):
            tool = self._bundle.tools[tool_id]
            if definition.allowed_tool_types and tool.tool_type not in definition.allowed_tool_types:
                continue
            # Show all tools, but mark recommended ones for this feature
            prefix = "★ " if self._is_recommended_for_feature(tool, definition.operation_type) else ""
            self._tool.addItem(f"{prefix}{tool.name} ({tool.cutting_diameter_mm:g} mm)", tool_id)
        if self._tool.count() == 0:
            self._tool.addItem("(no compatible tool)", None)

    def _is_recommended_for_feature(self, tool, op_type: OperationType) -> bool:
        """Heuristic: recommend tools that fit the feature dimensions."""
        if self._feature_node is None or self._feature_node.feature is None:
            return False
        feature = self._feature_node.feature
        if feature.kind == FeatureKind.HOLE and feature.radius is not None:
            # For holes: recommend drill slightly smaller than hole
            return tool.tool_type.value == "drill" and tool.cutting_diameter_mm <= feature.radius * 2
        if feature.kind == FeatureKind.FACE_PLANAR:
            # For faces: recommend end mill
            return tool.tool_type.value in ("end_mill", "ball", "bull")
        return False

    def exec(self) -> bool:
        return self._dialog.exec() == 1

    def operation_type(self) -> str:
        return self._type.currentData()

    def tool_id(self) -> str:
        return self._tool.currentData()

    def cooling_id(self) -> str:
        return self._cooling.currentData()

    def name(self) -> str | None:
        return self._name.text().strip() or None
