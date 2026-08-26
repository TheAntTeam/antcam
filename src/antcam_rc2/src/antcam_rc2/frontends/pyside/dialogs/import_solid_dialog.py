"""Dialog for importing a 3D solid with positioning options."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from antcam_rc2.core.geometry3d.scene import SolidScene
from antcam_rc2.core.project.models import Stock, StockOrigin


class ImportSolidDialog(QDialog):
    """Dialog for positioning a 3D solid when importing.

    Allows choosing the stock origin reference and fine-tuning the position.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        scene: SolidScene,
        stock: Stock,
    ) -> None:
        super().__init__(parent)
        self._scene = scene
        self._stock = stock
        self.setWindowTitle("Import 3D Solid - Positioning")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        bbox = scene.bounding_box()
        center = bbox.center
        self._bbox_label = QLabel(f"Bounding box: {bbox.width:.2f} × {bbox.height:.2f} × {bbox.depth:.2f} mm")
        layout.addWidget(self._bbox_label)

        # Origin reference
        self._origin = QComboBox(self)
        self._origin.addItems([origin.value for origin in StockOrigin])
        # Default to center_xy_top_z
        idx = self._origin.findText(StockOrigin.CENTER_XY_TOP_Z.value)
        if idx >= 0:
            self._origin.setCurrentIndex(idx)
        form.addRow("Stock Origin", self._origin)

        # Position offsets (relative to the chosen origin)
        self._offset_x = self._number(-center[0], decimals=2)
        self._offset_y = self._number(-center[1], decimals=2)
        self._offset_z = self._number(-center[2], decimals=2)

        form.addRow("Offset X (mm)", self._offset_x)
        form.addRow("Offset Y (mm)", self._offset_y)
        form.addRow("Offset Z (mm)", self._offset_z)

        layout.addLayout(form)

        # Preview info
        self._preview = QLabel("")
        self._preview.setWordWrap(True)
        layout.addWidget(self._preview)
        self._update_preview()

        # Connect signals
        self._origin.currentTextChanged.connect(self._update_preview)
        self._offset_x.valueChanged.connect(self._update_preview)
        self._offset_y.valueChanged.connect(self._update_preview)
        self._offset_z.valueChanged.connect(self._update_preview)

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

    def _update_preview(self) -> None:
        origin = StockOrigin(self._origin.currentText())
        ox, oy, oz = self._offset_x.value(), self._offset_y.value(), self._offset_z.value()

        if origin == StockOrigin.CENTER_XY_TOP_Z:
            desc = "Center XY, Top Z"
        elif origin == StockOrigin.CORNER_XY_TOP_Z:
            desc = "Corner XY, Top Z"
        elif origin == StockOrigin.CENTER_XY_ZERO_Z:
            desc = "Center XY, Zero Z"
        else:
            desc = "Corner XY, Zero Z"

        self._preview.setText(
            f"Origin: {desc}\n"
            f"Offset: X={ox:.2f}, Y={oy:.2f}, Z={oz:.2f} mm\n"
            f"The solid will be positioned relative to the stock using these values."
        )

    def result_offset(self) -> tuple[float, float, float]:
        """Return the (x, y, z) offset to apply to the solid."""
        return (self._offset_x.value(), self._offset_y.value(), self._offset_z.value())

    def result_origin(self) -> StockOrigin:
        """Return the selected stock origin reference."""
        return StockOrigin(self._origin.currentText())
