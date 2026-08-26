"""Operation parameters panel: schema-driven form bound to the selection."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QScrollArea, QVBoxLayout, QWidget

from antcam_rc2.core.operations.registry import build_standard_registry
from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController
from antcam_rc2.frontends.pyside.widgets.parameters_binder import ParametersBinder


class ParametersPanel(QWidget):
    """Edits the common and strategy-specific parameters of one operation."""

    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._registry = build_standard_registry()
        self._editing_operation_id: str | None = None

        layout = QVBoxLayout(self)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._binder = ParametersBinder(self)
        self._scroll.setWidget(self._binder)
        layout.addWidget(self._scroll)
        self._apply_button = QPushButton("Apply", self)
        self._apply_button.setObjectName("primary")
        layout.addWidget(self._apply_button)
        self._apply_button.clicked.connect(self._on_apply)

    def refresh(self) -> None:
        """Load the selected operation into the binder (once per selection)."""
        operation_id = self._controller.selected_operation_id
        project = self._controller.project
        operation = None
        if project is not None and operation_id is not None:
            operation = next((candidate for candidate in project.operations if candidate.id == operation_id), None)
        if operation is None:
            self.setEnabled(False)
            self._editing_operation_id = None
            return
        schema = self._registry.definition(operation.operation_type).parameters_model.model_json_schema()
        if operation.id != self._editing_operation_id:
            self._binder.rebuild(schema)
            self._editing_operation_id = operation.id
        self._binder.set_parameters(operation.parameters, schema)
        self.setEnabled(True)

    def _on_apply(self) -> None:
        if self._editing_operation_id is None:
            return
        parameters = self._binder.to_parameters()
        self._controller.update_operation_parameters(self._editing_operation_id, parameters)
