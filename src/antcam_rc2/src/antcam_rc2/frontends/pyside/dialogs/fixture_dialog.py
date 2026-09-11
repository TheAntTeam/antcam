"""Dialog for creating or editing a workholding fixture."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from antcam_rc2.core.identifiers import new_id
from antcam_rc2.core.project.fixture_library import FixtureLibrary
from antcam_rc2.core.project.models import Fixture, FixtureKind
from antcam_rc2.core.project.stock_geometry import stock_min_corner


class FixtureDialog(QDialog):
    """Collects fixture name, kind, dimensions and position.

    Pass an existing :class:`Fixture` to edit it (the id is preserved); pass
    ``None`` to create a new one (a fresh ``fix_*`` id is generated).

    If ``mesh_source`` is provided, the mesh file is copied to the fixture
    library on accept and the fixture's ``mesh_path`` is set to the relative
    path. A read-only field shows the mesh filename when available.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        fixture: Fixture | None = None,
        mesh_source: Path | None = None,
        stock=None,
        wcs=None,
    ) -> None:
        super().__init__(parent)
        self._fixture = fixture
        self._mesh_source = mesh_source
        self._stock = stock
        self._wcs = wcs
        self.setWindowTitle("Edit fixture" if fixture is not None else "Add fixture")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name = QLineEdit(fixture.name if fixture else "New fixture", self)
        self._kind = QComboBox(self)
        self._kind.addItems([kind.value for kind in FixtureKind])
        self._width = self._number(fixture.width_mm if fixture else 50.0)
        self._length = self._number(fixture.length_mm if fixture else 30.0)
        self._height = self._number(fixture.height_mm if fixture else 10.0)
        self._position_x = self._number(fixture.position_x_mm if fixture else 0.0, decimals=2)
        self._position_y = self._number(fixture.position_y_mm if fixture else 0.0, decimals=2)
        self._position_z = self._number(fixture.position_z_mm if fixture else 0.0, decimals=2)

        # Screw-specific fields
        self._screw_diameter = self._number(fixture.screw_diameter_mm if fixture and fixture.screw_diameter_mm else 5.0)
        self._screw_length = self._number(fixture.screw_length_mm if fixture and fixture.screw_length_mm else 20.0)
        self._hole_diameter = self._number(fixture.hole_diameter_mm if fixture and fixture.hole_diameter_mm else 5.5)
        self._screw_diameter.setVisible(False)
        self._screw_length.setVisible(False)
        self._hole_diameter.setVisible(False)

        # Mesh field (read-only, shows filename when mesh_path is set)
        self._mesh_label = QLabel("", self)
        self._mesh_label.setVisible(False)
        from PySide6.QtCore import Qt

        self._mesh_label.setTextInteractionFlags(
            self._mesh_label.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
        )

        if fixture is not None:
            index = self._kind.findText(fixture.kind.value)
            if index >= 0:
                self._kind.setCurrentIndex(index)

        # Track row indices for screw fields to hide/show labels
        self._screw_diameter_row = -1
        self._screw_length_row = -1
        self._hole_diameter_row = -1
        self._mesh_row = -1
        self._width_row = -1
        self._length_row = -1
        self._height_row = -1

        form.addRow("Name", self._name)
        form.addRow("Kind", self._kind)
        self._width_row = form.rowCount()
        form.addRow("Width (mm)", self._width)
        self._length_row = form.rowCount()
        form.addRow("Length (mm)", self._length)
        self._height_row = form.rowCount()
        form.addRow("Height (mm)", self._height)

        # Screw fields - capture row indices
        self._screw_diameter_row = form.rowCount()
        form.addRow("Screw Diameter (mm)", self._screw_diameter)
        self._screw_length_row = form.rowCount()
        form.addRow("Screw Length (mm)", self._screw_length)
        self._hole_diameter_row = form.rowCount()
        form.addRow("Hole Diameter (mm)", self._hole_diameter)

        # Mesh field - capture row index
        self._mesh_row = form.rowCount()
        form.addRow("Mesh", self._mesh_label)

        # Position fields with offset labels and absolute position tooltips
        self._position_x = self._number(fixture.position_x_mm if fixture else 0.0, decimals=2)
        self._position_y = self._number(fixture.position_y_mm if fixture else 0.0, decimals=2)
        self._position_z = self._number(fixture.position_z_mm if fixture else 0.0, decimals=2)
        self._update_position_tooltips()
        form.addRow("Offset X from stock corner (mm)", self._position_x)
        form.addRow("Offset Y from stock corner (mm)", self._position_y)
        form.addRow("Offset Z from stock corner (mm)", self._position_z)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._kind.currentTextChanged.connect(self._on_kind_changed)

        # Connect position changes to update tooltips
        self._position_x.valueChanged.connect(self._update_position_tooltips)
        self._position_y.valueChanged.connect(self._update_position_tooltips)
        self._position_z.valueChanged.connect(self._update_position_tooltips)

        if fixture is not None:
            self._update_screw_visibility(fixture.kind)
            self._update_mesh_visibility(fixture.mesh_path)
            # Hide dimensions for 3D mesh fixtures
            self._update_mesh_dimensions_visibility(fixture.mesh_path)

    @staticmethod
    def _number(value: float, *, decimals: int = 2) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(-100000.0, 100000.0)
        widget.setDecimals(decimals)
        widget.setValue(value)
        return widget

    def _on_kind_changed(self, kind_text: str) -> None:
        kind = FixtureKind(kind_text)
        self._update_screw_visibility(kind)
        self._update_mesh_dimensions_visibility(self._fixture.mesh_path if self._fixture else None)

    def _update_screw_visibility(self, kind: FixtureKind) -> None:
        is_screw = kind == FixtureKind.SCREW
        self._screw_diameter.setVisible(is_screw)
        self._screw_length.setVisible(is_screw)
        self._hole_diameter.setVisible(is_screw)
        # Hide fixed fixture dimensions for screw type OR when mesh is present
        has_mesh = bool(self._fixture.mesh_path if self._fixture else None)
        show_fixed_dims = not is_screw and not has_mesh
        self._width.setVisible(show_fixed_dims)
        self._length.setVisible(show_fixed_dims)
        self._height.setVisible(show_fixed_dims)
        # Also hide/show the labels
        vbox = self.layout()
        if vbox is not None:
            form_item = vbox.itemAt(0)
            if form_item is not None:
                form = form_item.layout()
                if form is not None:
                    qform = cast(QFormLayout, form)
                    for row in (
                        self._screw_diameter_row,
                        self._screw_length_row,
                        self._hole_diameter_row,
                        self._width_row,
                        self._length_row,
                        self._height_row,
                    ):
                        if row >= 0:
                            label_item = qform.itemAt(row, QFormLayout.ItemRole.LabelRole)
                            if label_item is not None:
                                label_widget = label_item.widget()
                                if label_widget is not None:
                                    # For fixed dimensions, show when NOT screw AND no mesh
                                    if row in (self._width_row, self._length_row, self._height_row):
                                        label_widget.setVisible(show_fixed_dims)
                                    else:
                                        label_widget.setVisible(is_screw)

    def _update_mesh_visibility(self, mesh_path: str | None) -> None:
        has_mesh = bool(mesh_path)
        self._mesh_label.setVisible(has_mesh)
        vbox = self.layout()
        if vbox is not None:
            form_item = vbox.itemAt(0)
            if form_item is not None:
                form = form_item.layout()
                if form is not None and self._mesh_row >= 0:
                    qform = cast(QFormLayout, form)
                    label_item = qform.itemAt(self._mesh_row, QFormLayout.ItemRole.LabelRole)
                    if label_item is not None:
                        label_widget = label_item.widget()
                        if label_widget is not None:
                            label_widget.setVisible(has_mesh)
        if has_mesh:
            # Show just the filename
            self._mesh_label.setText(Path(mesh_path).name)
        # Update dimensions visibility based on mesh
        self._update_mesh_dimensions_visibility(mesh_path)

    def _update_mesh_dimensions_visibility(self, mesh_path: str | None) -> None:
        """Hide and disable dimension fields (width/length/height) when a 3D mesh is present."""
        has_mesh = bool(mesh_path)
        is_screw = self._kind.currentText() == FixtureKind.SCREW.value if self._kind.currentText() else False
        show_fixed_dims = not has_mesh and not is_screw
        self._width.setVisible(show_fixed_dims)
        self._length.setVisible(show_fixed_dims)
        self._height.setVisible(show_fixed_dims)
        # Disable dimension fields when mesh is present (dimensions defined by mesh)
        self._width.setEnabled(not has_mesh)
        self._length.setEnabled(not has_mesh)
        self._height.setEnabled(not has_mesh)
        vbox = self.layout()
        if vbox is not None:
            form_item = vbox.itemAt(0)
            if form_item is not None:
                form = form_item.layout()
                if form is not None:
                    qform = cast(QFormLayout, form)
                    for row in (self._width_row, self._length_row, self._height_row):
                        if row >= 0:
                            label_item = qform.itemAt(row, QFormLayout.ItemRole.LabelRole)
                            if label_item is not None:
                                label_widget = label_item.widget()
                                if label_widget is not None:
                                    label_widget.setVisible(show_fixed_dims)

    def _update_position_tooltips(self) -> None:
        """Update tooltips on position spinboxes showing absolute WCS position."""
        if self._stock is not None and self._wcs is not None:
            stock_min_x, stock_min_y, stock_min_z = stock_min_corner(self._stock, self._wcs)
            abs_x = stock_min_x + self._position_x.value()
            abs_y = stock_min_y + self._position_y.value()
            abs_z = stock_min_z + self._position_z.value()
            self._position_x.setToolTip(f"Absolute X: {abs_x:.2f} mm")
            self._position_y.setToolTip(f"Absolute Y: {abs_y:.2f} mm")
            self._position_z.setToolTip(f"Absolute Z: {abs_z:.2f} mm")
        else:
            self._position_x.setToolTip("")
            self._position_y.setToolTip("")
            self._position_z.setToolTip("")

    def result_fixture(self) -> Fixture:
        """Return the edited fixture (reusing the id in edit mode).

        If a mesh_source was provided, it is copied to the fixture library
        and the fixture's mesh_path is set to the relative path.
        If editing an existing fixture with a mesh, the mesh_path is preserved.
        """
        kind = FixtureKind(self._kind.currentText())

        # For fixtures with mesh, preserve original mesh_path and dimensions
        if self._fixture is not None and self._fixture.mesh_path:
            fixture = Fixture(
                id=self._fixture.id,
                name=self._name.text().strip() or "New fixture",
                kind=kind,
                width_mm=self._fixture.width_mm,
                length_mm=self._fixture.length_mm,
                height_mm=self._fixture.height_mm,
                position_x_mm=self._position_x.value(),
                position_y_mm=self._position_y.value(),
                position_z_mm=self._position_z.value(),
                mesh_path=self._fixture.mesh_path,
                screw_diameter_mm=self._screw_diameter.value() if kind == FixtureKind.SCREW else None,
                screw_length_mm=self._screw_length.value() if kind == FixtureKind.SCREW else None,
                hole_diameter_mm=self._hole_diameter.value() if kind == FixtureKind.SCREW else None,
            )
        else:
            fixture = Fixture(
                id=self._fixture.id if self._fixture is not None else new_id("fix"),
                name=self._name.text().strip() or "New fixture",
                kind=kind,
                width_mm=self._width.value(),
                length_mm=self._length.value(),
                height_mm=self._height.value(),
                position_x_mm=self._position_x.value(),
                position_y_mm=self._position_y.value(),
                position_z_mm=self._position_z.value(),
                screw_diameter_mm=self._screw_diameter.value() if kind == FixtureKind.SCREW else None,
                screw_length_mm=self._screw_length.value() if kind == FixtureKind.SCREW else None,
                hole_diameter_mm=self._hole_diameter.value() if kind == FixtureKind.SCREW else None,
            )

        # Handle mesh copy to library
        if self._mesh_source is not None and self._mesh_source.exists():
            lib = FixtureLibrary.get_default()
            saved = lib.save_fixture(fixture, self._mesh_source)
            return saved

        return fixture
