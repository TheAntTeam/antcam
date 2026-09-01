"""Dialog for importing a 2D geometry with positioning options."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from antcam_rc2.core.io.scene import GeometryScene
from antcam_rc2.core.project.geometry_placement import (
    apply_placement,
    default_placement_for_scene,
    translation_for_center_xy,
)
from antcam_rc2.core.project.models import GeometryPlacement, Stock, StockOrigin, WorkCoordinateSystem


class ImportGeometryDialog(QDialog):
    """Dialog for positioning a 2D geometry when importing.

    Placement is non-destructive: returns ``GeometryPlacement`` applied lazily.
    Mirrors ``ImportSolidDialog`` but with StockOrigin, XY auto-center, Z offset
    from top, Rotation Z only and Mirror.
    """

    placement_changed = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        scene: GeometryScene,
        stock: Stock,
        wcs: WorkCoordinateSystem | None = None,
        placement: GeometryPlacement | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        self._stock = stock
        self._wcs = wcs
        self.setWindowTitle("Import 2D Geometry - Positioning")

        if placement is None:
            placement = default_placement_for_scene(scene, stock, wcs)
        self._default_placement = placement

        layout = QVBoxLayout(self)
        form = QFormLayout()

        bbox = scene.bounding_box()
        self._bbox_label = QLabel(f"Bounding box: {bbox.width:.2f} × {bbox.height:.2f} mm")
        layout.addWidget(self._bbox_label)
        has_entities = any(True for _ in scene.iter_entities())
        if has_entities:
            self._center_label = QLabel(
                f"Center: ({bbox.center.x:.2f}, {bbox.center.y:.2f})  "
                f"min=({bbox.min_x:.2f},{bbox.min_y:.2f}) max=({bbox.max_x:.2f},{bbox.max_y:.2f})"
            )
            layout.addWidget(self._center_label)

        # Origin reference (like solid)
        self._origin = QComboBox(self)
        self._origin.addItems([origin.value for origin in StockOrigin])
        idx = self._origin.findText(placement.stock_origin.value)
        if idx >= 0:
            self._origin.setCurrentIndex(idx)
        form.addRow("Stock Origin", self._origin)

        # Auto center XY
        self._auto_center = QCheckBox("Auto center XY on stock", self)
        self._auto_center.setChecked(bool(placement.auto_center_xy))
        form.addRow("", self._auto_center)

        # Offsets
        self._offset_x = self._number(placement.offset_x_mm, decimals=2)
        self._offset_y = self._number(placement.offset_y_mm, decimals=2)
        self._z_offset = self._number(placement.z_offset_from_top_mm, decimals=2)
        self._z_offset.setRange(-100000.0, 100000.0)
        form.addRow("Offset X (mm)", self._offset_x)
        form.addRow("Offset Y (mm)", self._offset_y)
        form.addRow("Z offset from top (mm)", self._z_offset)

        # Rotation Z only
        self._rot_z = self._number(placement.rotation_z_deg, decimals=1)
        self._rot_z.setRange(-360.0, 360.0)
        form.addRow("Rotation Z (°)", self._rot_z)

        # Mirror
        self._mirror_x = QCheckBox("Mirror X", self)
        self._mirror_x.setChecked(bool(placement.mirror_x))
        self._mirror_y = QCheckBox("Mirror Y", self)
        self._mirror_y.setChecked(bool(placement.mirror_y))
        form.addRow("", self._mirror_x)
        form.addRow("", self._mirror_y)

        layout.addLayout(form)

        # Presets
        presets_layout = QHBoxLayout()
        self._btn_center = QPushButton("Center XY", self)
        self._btn_top = QPushButton("Snap to Top", self)
        self._btn_bottom = QPushButton("Snap to Bottom", self)
        self._btn_reset = QPushButton("Reset", self)
        for btn in (self._btn_center, self._btn_top, self._btn_bottom, self._btn_reset):
            presets_layout.addWidget(btn)
        layout.addLayout(presets_layout)

        self._preview = QLabel("")
        self._preview.setWordWrap(True)
        layout.addWidget(self._preview)

        # Wiring
        self._origin.currentTextChanged.connect(self._update_preview)
        self._auto_center.toggled.connect(self._on_auto_toggled)
        for w in (self._offset_x, self._offset_y, self._z_offset, self._rot_z):
            w.valueChanged.connect(self._update_preview)
        self._mirror_x.toggled.connect(self._update_preview)
        self._mirror_y.toggled.connect(self._update_preview)
        self._btn_center.clicked.connect(self._preset_center)
        self._btn_top.clicked.connect(self._preset_snap_top)
        self._btn_bottom.clicked.connect(self._preset_snap_bottom)
        self._btn_reset.clicked.connect(self._preset_reset)

        self._on_auto_toggled(self._auto_center.isChecked())
        self._update_preview()
        self.placement_changed.emit(self.result_placement())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _number(value: float, *, decimals: int = 2) -> QDoubleSpinBox:
        w = QDoubleSpinBox()
        w.setRange(-100000.0, 100000.0)
        w.setDecimals(decimals)
        w.setValue(value)
        return w

    def _on_auto_toggled(self, checked: bool) -> None:
        self._offset_x.setEnabled(not checked)
        self._offset_y.setEnabled(not checked)
        self._update_preview()

    def _preset_center(self) -> None:
        origin = StockOrigin(self._origin.currentText())
        dx, dy = translation_for_center_xy(self._scene, self._stock, self._wcs, desired_origin=origin)
        self._offset_x.setValue(float(dx))
        self._offset_y.setValue(float(dy))

    def _preset_snap_top(self) -> None:
        self._z_offset.setValue(0.0)

    def _preset_snap_bottom(self) -> None:
        # Place at bottom: z = top + offset == bottom => offset = -height
        h = float(self._stock.height_mm)
        self._z_offset.setValue(float(-h))

    def _preset_reset(self) -> None:
        p = self._default_placement
        self._origin.setCurrentText(p.stock_origin.value)
        self._auto_center.setChecked(bool(p.auto_center_xy))
        self._offset_x.setValue(float(p.offset_x_mm))
        self._offset_y.setValue(float(p.offset_y_mm))
        self._z_offset.setValue(float(p.z_offset_from_top_mm))
        self._rot_z.setValue(float(p.rotation_z_deg))
        self._mirror_x.setChecked(bool(p.mirror_x))
        self._mirror_y.setChecked(bool(p.mirror_y))

    def _update_preview(self) -> None:
        placement = self.result_placement()
        has_entities = any(True for _ in self._scene.iter_entities())
        if has_entities:
            placed = apply_placement(self._scene, placement, self._stock, self._wcs)
            pb = placed.bounding_box()
            self._preview.setText(
                f"Origin: {placement.stock_origin.value} Auto center XY: {placement.auto_center_xy}\n"
                f"Offset: X={placement.offset_x_mm:.2f}, Y={placement.offset_y_mm:.2f}, "
                f"Z from top={placement.z_offset_from_top_mm:.2f} mm\n"
                f"Rotation Z={placement.rotation_z_deg:.1f}° Mirror X={placement.mirror_x} Y={placement.mirror_y}\n"
                f"Placed bbox: ({pb.min_x:.1f},{pb.min_y:.1f}) → ({pb.max_x:.1f},{pb.max_y:.1f})"
            )
        else:
            self._preview.setText(
                f"Origin: {placement.stock_origin.value} Auto: {placement.auto_center_xy} (empty)"
            )
        try:
            self.placement_changed.emit(placement)
        except Exception:
            pass

    def result_placement(self) -> GeometryPlacement:
        return GeometryPlacement(
            offset_x_mm=float(self._offset_x.value()),
            offset_y_mm=float(self._offset_y.value()),
            z_offset_from_top_mm=float(self._z_offset.value()),
            rotation_z_deg=float(self._rot_z.value()),
            mirror_x=bool(self._mirror_x.isChecked()),
            mirror_y=bool(self._mirror_y.isChecked()),
            auto_center_xy=bool(self._auto_center.isChecked()),
            stock_origin=StockOrigin(self._origin.currentText()),
        )
