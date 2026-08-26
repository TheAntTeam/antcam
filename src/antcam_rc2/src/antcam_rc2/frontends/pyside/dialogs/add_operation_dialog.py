"""Dialog for adding an operation: type, tool (filtered), cooling, name."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QVBoxLayout

from antcam_rc2.core.operations.registry import build_standard_registry


class AddOperationDialog(QDialog):
    """Collects the fields required by :meth:`ProjectService.add_operation`.

    The tool list is filtered by the registry's ``allowed_tool_types`` for the
    selected operation type, so incompatible tools can never be selected.
    """

    def __init__(self, services, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add operation")
        self._services = services
        self._registry = build_standard_registry()
        self._bundle = services.catalog_repository.load()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._type = QComboBox(self)
        for definition in self._registry.definitions():
            self._type.addItem(definition.display_name, definition.operation_type.value)
        self._tool = QComboBox(self)
        self._cooling = QComboBox(self)
        for cooling_id in sorted(self._bundle.cooling):
            self._cooling.addItem(self._bundle.cooling[cooling_id].name, cooling_id)
        self._name = QLineEdit(self)
        form.addRow("Type", self._type)
        form.addRow("Tool", self._tool)
        form.addRow("Cooling", self._cooling)
        form.addRow("Name", self._name)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._type.currentIndexChanged.connect(self._reload_tools)
        self._reload_tools()

    def _reload_tools(self) -> None:
        definition = self._registry.definition(self._type.currentData())
        self._tool.clear()
        for tool_id in sorted(self._bundle.tools):
            tool = self._bundle.tools[tool_id]
            if definition.allowed_tool_types and tool.tool_type not in definition.allowed_tool_types:
                continue
            self._tool.addItem(f"{tool.name} ({tool.cutting_diameter_mm:g} mm)", tool_id)
        if self._tool.count() == 0:
            self._tool.addItem("(no compatible tool)", None)

    def operation_type(self) -> str:
        return self._type.currentData()

    def tool_id(self) -> str:
        return self._tool.currentData()

    def cooling_id(self) -> str:
        return self._cooling.currentData()

    def name(self) -> str | None:
        return self._name.text().strip() or None
