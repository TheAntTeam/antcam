"""Project/setup panel: machine, stock, WCS and fixtures."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from antcam_rc2.core.project.models import FixtureKind, Stock, StockOrigin
from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController


class ProjectPanel(QWidget):
    """Edit machine/stock/WCS and list fixtures for the current project."""

    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._building = False

        layout = QVBoxLayout(self)
        self._machine_combo = QComboBox(self)
        layout.addWidget(self._make_group("Machine", self._machine_combo))

        stock_form = QFormLayout()
        self._width = self._number(300.0)
        self._length = self._number(300.0)
        self._height = self._number(100.0)
        self._material = QComboBox(self)
        self._origin = QComboBox(self)
        self._origin.addItems([origin.value for origin in StockOrigin])
        for label, widget in (
            ("Width (mm)", self._width),
            ("Length (mm)", self._length),
            ("Height (mm)", self._height),
            ("Material", self._material),
            ("Origin", self._origin),
        ):
            stock_form.addRow(label, widget)
        stock_widget = QWidget(self)
        stock_widget.setLayout(stock_form)
        layout.addWidget(self._make_group("Stock", stock_widget))

        self._fixtures = QListWidget(self)
        fixture_buttons = QHBoxLayout()
        add_fixture = QPushButton("Add", self)
        edit_fixture = QPushButton("Edit", self)
        remove_fixture = QPushButton("Remove", self)
        remove_fixture.setObjectName("danger")
        fixture_buttons.addWidget(add_fixture)
        fixture_buttons.addWidget(edit_fixture)
        fixture_buttons.addWidget(remove_fixture)

        # Fixture library buttons
        fixture_lib_buttons = QHBoxLayout()
        import_mesh = QPushButton("Import Mesh…", self)
        save_to_lib = QPushButton("Save to Library", self)
        load_from_lib = QPushButton("Load from Library…", self)
        fixture_lib_buttons.addWidget(import_mesh)
        fixture_lib_buttons.addWidget(save_to_lib)
        fixture_lib_buttons.addWidget(load_from_lib)

        fixtures_widget = QWidget(self)
        fixtures_layout = QVBoxLayout(fixtures_widget)
        fixtures_layout.addWidget(self._fixtures)
        fixtures_layout.addLayout(fixture_buttons)
        fixtures_layout.addLayout(fixture_lib_buttons)
        layout.addWidget(self._make_group("Fixtures", fixtures_widget))

        file_buttons = QHBoxLayout()
        open_button = QPushButton("Open project", self)
        save_button = QPushButton("Save project", self)
        file_buttons.addWidget(open_button)
        file_buttons.addWidget(save_button)
        layout.addLayout(file_buttons)

        self._machine_combo.currentIndexChanged.connect(self._on_machine_changed)
        add_fixture.clicked.connect(self._on_add_fixture)
        edit_fixture.clicked.connect(self._on_edit_fixture)
        remove_fixture.clicked.connect(self._on_remove_fixture)
        import_mesh.clicked.connect(self._on_import_fixture_mesh)
        save_to_lib.clicked.connect(self._on_save_fixture_to_library)
        load_from_lib.clicked.connect(self._on_load_fixture_from_library)
        open_button.clicked.connect(self._on_open)
        save_button.clicked.connect(self._on_save)
        for widget in (self._width, self._length, self._height):
            widget.valueChanged.connect(self._on_stock_changed)
        self._material.currentIndexChanged.connect(self._on_stock_changed)
        self._origin.currentIndexChanged.connect(self._on_stock_changed)

    @staticmethod
    def _number(value: float) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(0.1, 100000.0)
        widget.setDecimals(2)
        widget.setValue(value)
        return widget

    @staticmethod
    def _make_group(title: str, content: QWidget) -> QGroupBox:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.addWidget(content)
        return group

    # ------------------------------------------------------------------ refresh
    def refresh(self) -> None:
        """Re-read the current project into the widgets."""
        self._building = True
        try:
            project = self._controller.project
            if project is None:
                self.setEnabled(False)
                return
            self.setEnabled(True)
            bundle = self._controller.services.catalog_repository.load()
            self._machine_combo.clear()
            for machine_id in sorted(bundle.machines):
                self._machine_combo.addItem(bundle.machines[machine_id].name, machine_id)
            index = self._machine_combo.findData(project.machine_id)
            if index >= 0:
                self._machine_combo.setCurrentIndex(index)
            self._material.clear()
            for material_id in sorted(bundle.materials):
                self._material.addItem(bundle.materials[material_id].name, material_id)
            material_index = self._material.findData(project.stock.material_id)
            if material_index >= 0:
                self._material.setCurrentIndex(material_index)
            self._width.setValue(project.stock.width_mm)
            self._length.setValue(project.stock.length_mm)
            self._height.setValue(project.stock.height_mm)
            origin_index = self._origin.findText(project.stock.origin.value)
            if origin_index >= 0:
                self._origin.setCurrentIndex(origin_index)
            self._fixtures.clear()
            for fixture in project.fixtures:
                if fixture.kind == FixtureKind.SCREW:
                    screw_info = ""
                    if fixture.screw_diameter_mm:
                        screw_info += f" ⌀{fixture.screw_diameter_mm:g}mm"
                    if fixture.screw_length_mm:
                        screw_info += f" L{fixture.screw_length_mm:g}mm"
                    if fixture.hole_diameter_mm:
                        screw_info += f" hole⌀{fixture.hole_diameter_mm:g}mm"
                    self._fixtures.addItem(
                        f"{fixture.name} ({fixture.kind.value}){screw_info} "
                        f"@ ({fixture.position_x_mm:g}, {fixture.position_y_mm:g}, {fixture.position_z_mm:g})"
                    )
                else:
                    self._fixtures.addItem(
                        f"{fixture.name} ({fixture.kind.value}) "
                        f"{fixture.width_mm:g}×{fixture.length_mm:g}×{fixture.height_mm:g} mm "
                        f"@ ({fixture.position_x_mm:g}, {fixture.position_y_mm:g}, {fixture.position_z_mm:g})"
                    )
        finally:
            self._building = False

    # ------------------------------------------------------------------ actions
    def _on_machine_changed(self) -> None:
        if self._building or self._controller.project is None:
            return
        machine_id = self._machine_combo.currentData()
        if machine_id:
            self._controller.select_machine(machine_id)

    def _on_stock_changed(self) -> None:
        if self._building or self._controller.project is None:
            return
        material_id = self._material.currentData() or "aluminum_6061"
        self._controller.replace_stock(
            Stock(
                width_mm=self._width.value(),
                length_mm=self._length.value(),
                height_mm=self._height.value(),
                material_id=material_id,
                origin=StockOrigin(self._origin.currentText()),
            )
        )

    def _on_add_fixture(self) -> None:
        if self._controller.project is None:
            return
        from antcam_rc2.frontends.pyside.dialogs.fixture_dialog import FixtureDialog

        dialog = FixtureDialog(self)
        if dialog.exec():
            self._controller.add_fixture(dialog.result_fixture())

    def _on_edit_fixture(self) -> None:
        row = self._fixtures.currentRow()
        project = self._controller.project
        if project is None or row < 0 or row >= len(project.fixtures):
            return
        from antcam_rc2.frontends.pyside.dialogs.fixture_dialog import FixtureDialog

        fixture = project.fixtures[row]
        dialog = FixtureDialog(self, fixture=fixture)
        if dialog.exec():
            self._controller.replace_fixture(fixture.id, dialog.result_fixture())

    def _on_remove_fixture(self) -> None:
        row = self._fixtures.currentRow()
        project = self._controller.project
        if project is None or row < 0 or row >= len(project.fixtures):
            return
        self._controller.remove_fixture(project.fixtures[row].id)

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "AntCAM project (*.antcam.json)")
        if path:
            self._controller.open_project(Path(path))

    def _on_save(self) -> None:
        if self._controller.project is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save project", f"{self._controller.project.name}.antcam.json", "AntCAM project (*.antcam.json)"
        )
        if path:
            self._controller.save_project(Path(path))

    def _on_import_fixture_mesh(self) -> None:
        if self._controller.project is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Fixture Mesh", "", "3D Models (*.step *.stp *.stl);;All files (*)"
        )
        if path:
            self._controller.import_fixture_mesh(Path(path))

    def _on_save_fixture_to_library(self) -> None:
        row = self._fixtures.currentRow()
        project = self._controller.project
        if project is None or row < 0 or row >= len(project.fixtures):
            return
        fixture = project.fixtures[row]
        self._controller.save_fixture_to_library(fixture)

    def _on_load_fixture_from_library(self) -> None:
        from antcam_rc2.frontends.pyside.dialogs.fixture_library_dialog import FixtureLibraryDialog

        dialog = FixtureLibraryDialog(self)
        if dialog.exec():
            fixture_id = dialog.selected_fixture_id()
            if fixture_id:
                self._controller.load_fixture_from_library(fixture_id)
