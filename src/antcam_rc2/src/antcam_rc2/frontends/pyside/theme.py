"""Dark theme: palette, toolpath colors and application-wide stylesheet."""

from __future__ import annotations

from antcam_rc2.core.project.models import OperationType
from antcam_rc2.core.rendering.scene_graph import RGBA

# Palette (dark, modern)
SURFACE = "#1e1f22"
SURFACE_ALT = "#26272b"
SURFACE_RAISED = "#2d2e33"
BORDER = "#34363b"
TEXT = "#d7d8da"
TEXT_DIM = "#9a9ca1"
ACCENT = "#4da3ff"
ACCENT_HOVER = "#6cb3ff"
DANGER = "#e5534b"
OK = "#57ab5a"
WARNING = "#d29922"
INFO = "#4da3ff"

# Render graph colors (RGBA floats)
# Stock color is resolved from the material family: gray for metals, a muted
# orange for woods (see ``stock_color_for_material_family``).
STOCK_COLOR_METAL: RGBA = (0.58, 0.60, 0.64, 0.35)
STOCK_COLOR_WOOD: RGBA = (0.80, 0.52, 0.26, 0.35)
STOCK_COLOR: RGBA = STOCK_COLOR_METAL
STOCK_EDGE_COLOR: RGBA = (0.58, 0.60, 0.64, 1.0)
# Fixture colors (solid red, opaque)
FIXTURE_COLOR_SOLID: RGBA = (0.7, 0.15, 0.15, 1.0)
FIXTURE_OUTLINE_COLOR: RGBA = (0.9, 0.3, 0.3, 1.0)
FIXTURE_MESH_COLOR: RGBA = (0.65, 0.1, 0.1, 1.0)
# Deprecated alias for backward compatibility
FIXTURE_COLOR: RGBA = FIXTURE_COLOR_SOLID
WORK_AREA_COLOR: RGBA = (0.38, 0.40, 0.45, 1.0)
ORIGIN_COLOR: RGBA = (0.91, 0.29, 0.29, 1.0)
GRID_COLOR: RGBA = (0.18, 0.19, 0.22, 1.0)
SELECTION_COLOR: RGBA = (0.30, 0.64, 1.0, 1.0)
RAPID_COLOR: RGBA = (0.62, 0.64, 0.68, 1.0)
SIMULATED_MATERIAL_COLOR: RGBA = (0.86, 0.62, 0.22, 1.0)  # machined material surface

# Toolpath color per operation family (milling amber, drilling cyan, carving green)
_TOOLPATH_COLORS: dict[OperationType, RGBA] = {
    OperationType.FACE_TOP: (0.95, 0.72, 0.25, 1.0),
    OperationType.ROUGHING: (0.93, 0.55, 0.23, 1.0),
    OperationType.FACING: (0.95, 0.66, 0.28, 1.0),
    OperationType.POCKETING: (0.96, 0.60, 0.20, 1.0),
    OperationType.PROFILING: (0.98, 0.72, 0.34, 1.0),
    OperationType.SLOTTING: (0.93, 0.48, 0.20, 1.0),
    OperationType.T_SLOTTING: (0.91, 0.42, 0.22, 1.0),
    OperationType.HOLES: (0.30, 0.80, 0.90, 1.0),
    OperationType.DRILL: (0.25, 0.78, 0.88, 1.0),
    OperationType.BORING: (0.32, 0.84, 0.92, 1.0),
    OperationType.HOLE_POCKETING: (0.20, 0.72, 0.86, 1.0),
    OperationType.THREAD_MILLING: (0.45, 0.88, 0.95, 1.0),
    OperationType.TAPPING: (0.55, 0.90, 0.96, 1.0),
    OperationType.V_CARVE_ROUGHING: (0.42, 0.82, 0.48, 1.0),
    OperationType.V_CARVING: (0.38, 0.78, 0.44, 1.0),
    OperationType.ENGRAVING: (0.50, 0.86, 0.55, 1.0),
    OperationType.CHAMFERING: (0.56, 0.88, 0.60, 1.0),
    OperationType.FILLETTING: (0.62, 0.90, 0.65, 1.0),
}


def toolpath_color(operation_type: OperationType) -> RGBA:
    """The render color for an operation's toolpath."""
    return _TOOLPATH_COLORS.get(operation_type, (0.8, 0.8, 0.8, 1.0))


def stock_color_for_material_family(family: str) -> RGBA:
    """The translucent stock render color for a material family."""
    return STOCK_COLOR_WOOD if family == "wood" else STOCK_COLOR_METAL


def opaque(color: RGBA) -> RGBA:
    """Return an opaque copy of an RGBA color."""
    return (color[0], color[1], color[2], 1.0)


def color_to_hex(color: RGBA) -> str:
    """Convert RGBA floats to a ``#rrggbb`` hex string."""
    return f"#{round(color[0] * 255):02x}{round(color[1] * 255):02x}{round(color[2] * 255):02x}"


def build_qss() -> str:
    """Return the application stylesheet (QSS)."""
    return f"""
QMainWindow, QDialog {{
    background-color: {SURFACE};
}}
QWidget {{
    color: {TEXT};
    font-size: 12px;
}}
QMenuBar {{
    background-color: {SURFACE_ALT};
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item:selected {{
    background-color: {SURFACE_RAISED};
}}
QToolBar {{
    background-color: {SURFACE_ALT};
    border: none;
    spacing: 4px;
}}
QStatusBar {{
    background-color: {SURFACE_ALT};
    border-top: 1px solid {BORDER};
}}
QSplitter::handle {{
    background-color: {BORDER};
}}
QListWidget, QTableWidget, QPlainTextEdit, QTreeWidget {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}
QListWidget::item {{
    padding: 4px 6px;
}}
QListWidget::item:selected {{
    background-color: {ACCENT};
    color: #ffffff;
}}
QPushButton {{
    background-color: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 12px;
}}
QPushButton:hover {{
    border-color: {ACCENT};
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
}}
QPushButton#primary {{
    background-color: {ACCENT};
    color: #ffffff;
    border: none;
}}
QPushButton#primary:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton#danger {{
    color: {DANGER};
}}
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
    background-color: {SURFACE_RAISED};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 3px 6px;
    selection-background-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    color: {TEXT_DIM};
}}
QScrollBar:vertical {{
    background: {SURFACE};
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: {SURFACE_RAISED};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
}}
"""


def build_palette():
    """Return the QPalette for the dark theme."""
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE_ALT))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(SURFACE_RAISED))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(SURFACE_RAISED))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    return palette


def apply(app) -> None:
    """Apply the theme to a QApplication instance."""
    app.setPalette(build_palette())
    app.setStyleSheet(build_qss())


__all__ = [
    "ACCENT",
    "BORDER",
    "DANGER",
    "FIXTURE_COLOR",
    "FIXTURE_COLOR_SOLID",
    "FIXTURE_OUTLINE_COLOR",
    "FIXTURE_MESH_COLOR",
    "GRID_COLOR",
    "INFO",
    "OK",
    "ORIGIN_COLOR",
    "RAPID_COLOR",
    "SELECTION_COLOR",
    "SIMULATED_MATERIAL_COLOR",
    "STOCK_COLOR",
    "STOCK_COLOR_METAL",
    "STOCK_COLOR_WOOD",
    "STOCK_EDGE_COLOR",
    "opaque",
    "stock_color_for_material_family",
    "SURFACE",
    "SURFACE_ALT",
    "SURFACE_RAISED",
    "TEXT",
    "TEXT_DIM",
    "WARNING",
    "WORK_AREA_COLOR",
    "apply",
    "build_palette",
    "build_qss",
    "color_to_hex",
    "toolpath_color",
]
