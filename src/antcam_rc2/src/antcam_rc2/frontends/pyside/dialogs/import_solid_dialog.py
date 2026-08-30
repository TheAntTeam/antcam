"""Dialog for importing a 3D solid with positioning options."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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

from antcam_rc2.core.geometry3d.scene import SolidScene
from antcam_rc2.core.project.models import SolidPlacement, Stock, StockOrigin, WorkCoordinateSystem
from antcam_rc2.core.project.solid_placement import (
    apply_placement,
    default_placement_for_scene,
    translation_after_snap_z,
    translation_for_center_on_stock,
)


class ImportSolidDialog(QDialog):
    """Dialog for positioning a 3D solid when importing.

    Allows choosing the stock origin reference and fine-tuning the position.
    The dialog is non-destructive: it returns a :class:`SolidPlacement` that
    is applied lazily at render/toolpath time.

    Live preview: emits :attr:`placement_changed` on every value change so the
    caller can update the viewport while the dialog is open.
    """

    placement_changed = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        scene: SolidScene,
        stock: Stock,
        wcs: WorkCoordinateSystem | None = None,
        placement: SolidPlacement | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        self._stock = stock
        self._wcs = wcs
        self.setWindowTitle("Import 3D Solid - Positioning")

        # Compute default placement (center XY + snap Z) when none supplied.
        if placement is None:
            placement = default_placement_for_scene(scene, stock, wcs)
        self._default_placement = placement

        layout = QVBoxLayout(self)
        form = QFormLayout()

        bbox = scene.bounding_box()
        self._bbox_label = QLabel(f"Bounding box: {bbox.width:.2f} × {bbox.height:.2f} × {bbox.depth:.2f} mm")
        layout.addWidget(self._bbox_label)
        if not bbox.is_empty:
            self._center_label = QLabel(
                f"Center: ({bbox.center[0]:.2f}, {bbox.center[1]:.2f}, {bbox.center[2]:.2f})  "
                f"min_z={bbox.min_z:.2f}  max_z={bbox.max_z:.2f}"
            )
            layout.addWidget(self._center_label)

        # Origin reference
        self._origin = QComboBox(self)
        self._origin.addItems([origin.value for origin in StockOrigin])
        idx = self._origin.findText(placement.stock_origin.value)
        if idx >= 0:
            self._origin.setCurrentIndex(idx)
        else:
            idx = self._origin.findText(StockOrigin.CENTER_XY_TOP_Z.value)
            if idx >= 0:
                self._origin.setCurrentIndex(idx)
        form.addRow("Stock Origin", self._origin)

        tx, ty, tz = placement.translation
        rx, ry, rz = placement.rotation_deg
        # Position offsets
        self._offset_x = self._number(tx, decimals=2)
        self._offset_y = self._number(ty, decimals=2)
        self._offset_z = self._number(tz, decimals=2)
        form.addRow("Offset X (mm)", self._offset_x)
        form.addRow("Offset Y (mm)", self._offset_y)
        form.addRow("Offset Z (mm)", self._offset_z)

        # Rotation (Euler deg, ZYX order)
        self._rot_x = self._number(rx, decimals=1)
        self._rot_x.setRange(-360.0, 360.0)
        self._rot_y = self._number(ry, decimals=1)
        self._rot_y.setRange(-360.0, 360.0)
        self._rot_z = self._number(rz, decimals=1)
        self._rot_z.setRange(-360.0, 360.0)
        form.addRow("Rotation X (°)", self._rot_x)
        form.addRow("Rotation Y (°)", self._rot_y)
        form.addRow("Rotation Z (°)", self._rot_z)

        self._scale = self._number(float(placement.scale), decimals=3)
        self._scale.setRange(0.01, 100.0)
        self._scale.setSingleStep(0.1)
        form.addRow("Scale", self._scale)

        layout.addLayout(form)

        # Presets
        presets_layout = QHBoxLayout()
        self._btn_center = QPushButton("Center on Stock", self)
        self._btn_top = QPushButton("Snap to Top", self)
        self._btn_zero = QPushButton("Snap to Bottom", self)
        self._btn_reset = QPushButton("Reset", self)
        for btn in (self._btn_center, self._btn_top, self._btn_zero, self._btn_reset):
            presets_layout.addWidget(btn)
        layout.addLayout(presets_layout)

        # Preview info
        self._preview = QLabel("")
        self._preview.setWordWrap(True)
        layout.addWidget(self._preview)

        # Wiring
        self._origin.currentTextChanged.connect(self._update_preview)
        for w in (self._offset_x, self._offset_y, self._offset_z, self._rot_x, self._rot_y, self._rot_z, self._scale):
            w.valueChanged.connect(self._update_preview)
        self._btn_center.clicked.connect(self._preset_center)
        self._btn_top.clicked.connect(self._preset_snap_top)
        self._btn_zero.clicked.connect(self._preset_snap_zero)
        self._btn_reset.clicked.connect(self._preset_reset)

        self._update_preview()
        # Emit initial placement so caller can show live preview immediately.
        self.placement_changed.emit(self.result_placement())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _number(value: float, *, decimals: int = 2) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(-100000.0, 100000.0)
        widget.setDecimals(decimals)
        widget.setValue(value)
        return widget

    # -- presets -----------------------------------------------------------
    def _preset_center(self) -> None:
        """Place the solid's center at the stock center (XYZ), preserving rotation/scale."""
        rot = (self._rot_x.value(), self._rot_y.value(), self._rot_z.value())
        sc = float(self._scale.value())
        dx, dy, dz = translation_for_center_on_stock(
            self._scene, self._stock, self._wcs, rotation_deg=rot, scale=sc
        )
        self._offset_x.setValue(float(dx))
        self._offset_y.setValue(float(dy))
        self._offset_z.setValue(float(dz))

    def _preset_snap_top(self) -> None:
        """Snap Z so placed max_z == stock_top_z, preserving X/Y and rotation/scale."""
        cur = self.result_placement()
        tx, ty, tz = translation_after_snap_z(self._scene, self._stock, self._wcs, cur, to_top=True)
        # Only Z changes; keep X/Y as-is.
        self._offset_z.setValue(float(tz))
        # Ensure spinboxes reflect preserved XY (no change needed, but keep explicit).
        self._offset_x.setValue(float(tx))
        self._offset_y.setValue(float(ty))

    def _preset_snap_zero(self) -> None:
        """Snap Z so placed min_z == stock_bottom_z, preserving X/Y and rotation/scale."""
        cur = self.result_placement()
        tx, ty, tz = translation_after_snap_z(self._scene, self._stock, self._wcs, cur, to_top=False)
        self._offset_z.setValue(float(tz))
        self._offset_x.setValue(float(tx))
        self._offset_y.setValue(float(ty))

    def _preset_reset(self) -> None:
        p = self._default_placement
        self._origin.setCurrentText(p.stock_origin.value)
        self._offset_x.setValue(p.translation[0])
        self._offset_y.setValue(p.translation[1])
        self._offset_z.setValue(p.translation[2])
        self._rot_x.setValue(p.rotation_deg[0])
        self._rot_y.setValue(p.rotation_deg[1])
        self._rot_z.setValue(p.rotation_deg[2])
        self._scale.setValue(float(p.scale))

    def _update_preview(self) -> None:
        origin = StockOrigin(self._origin.currentText())
        placement = self.result_placement()
        bbox = self._scene.bounding_box()
        if not bbox.is_empty:
            placed = apply_placement(self._scene, placement)
            pb = placed.bounding_box()
            self._preview.setText(
                f"Origin: {origin.value}\n"
                f"Translation: X={placement.translation[0]:.2f}, "
                f"Y={placement.translation[1]:.2f}, Z={placement.translation[2]:.2f} mm\n"
                f"Rotation: X={placement.rotation_deg[0]:.1f}° "
                f"Y={placement.rotation_deg[1]:.1f}° Z={placement.rotation_deg[2]:.1f}° "
                f"Scale={placement.scale:.3f}\n"
                f"Placed bbox: ({pb.min_x:.1f},{pb.min_y:.1f},{pb.min_z:.1f}) → "
                f"({pb.max_x:.1f},{pb.max_y:.1f},{pb.max_z:.1f})"
            )
        else:
            self._preview.setText(f"Origin: {origin.value}  (empty scene)")
        # Notify live preview listener (e.g. ProjectController).
        try:
            self.placement_changed.emit(placement)
        except Exception:
            pass

    # -- results -----------------------------------------------------------
    def result_placement(self) -> SolidPlacement:
        """Return the non-destructive placement chosen in the dialog."""
        return SolidPlacement(
            translation=(self._offset_x.value(), self._offset_y.value(), self._offset_z.value()),
            rotation_deg=(self._rot_x.value(), self._rot_y.value(), self._rot_z.value()),
            scale=float(self._scale.value()),
            stock_origin=StockOrigin(self._origin.currentText()),
        )

    def result_offset(self) -> tuple[float, float, float]:
        """Backward compat: return translation part."""
        p = self.result_placement()
        return p.translation

    def result_origin(self) -> StockOrigin:
        """Backward compat: return stock_origin."""
        return self.result_placement().stock_origin
