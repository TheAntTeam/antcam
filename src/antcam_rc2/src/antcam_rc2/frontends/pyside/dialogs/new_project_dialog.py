"""Dialog for creating a new project."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLineEdit, QVBoxLayout

from antcam_rc2.core.project.models import Stock, StockOrigin


class NewProjectDialog(QDialog):
    """Collects name, machine and stock dimensions for a new project."""

    def __init__(self, services, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New project")
        self._services = services
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._name = QLineEdit("New project", self)
        self._machine = QComboBox(self)
        bundle = services.catalog_repository.load()
        for machine_id in sorted(bundle.machines):
            self._machine.addItem(bundle.machines[machine_id].name, machine_id)
        self._width = self._spin(200.0)
        self._length = self._spin(200.0)
        self._height = self._spin(100.0)
        self._material = QComboBox(self)
        for material_id in sorted(bundle.materials):
            self._material.addItem(bundle.materials[material_id].name, material_id)
        form.addRow("Name", self._name)
        form.addRow("Machine", self._machine)
        form.addRow("Stock width (mm)", self._width)
        form.addRow("Stock length (mm)", self._length)
        form.addRow("Stock height (mm)", self._height)
        form.addRow("Material", self._material)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _spin(value: float) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(0.1, 100000.0)
        widget.setDecimals(2)
        widget.setValue(value)
        return widget

    def machine_id(self) -> str:
        return self._machine.currentData()

    def stock(self) -> Stock:
        return Stock(
            width_mm=self._width.value(),
            length_mm=self._length.value(),
            height_mm=self._height.value(),
            material_id=self._material.currentData() or "aluminum_6061",
            origin=StockOrigin.CORNER_XY_TOP_Z,
        )

    def name(self) -> str:
        return self._name.text().strip() or "New project"
