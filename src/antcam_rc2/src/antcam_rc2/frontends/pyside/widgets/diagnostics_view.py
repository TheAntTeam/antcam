"""Colored diagnostics list for toolpath and simulation results."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtWidgets import QListWidget, QListWidgetItem

from antcam_rc2.frontends.pyside import theme


class DiagnosticsView(QListWidget):
    """Displays a list of diagnostic-like entries, severity-colored."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setWordWrap(True)

    def set_diagnostics(self, diagnostics: Sequence[Any]) -> None:
        """Show diagnostic-like entries (``code``/``message``/``severity.value``)."""
        self.clear()
        for diagnostic in diagnostics:
            item = QListWidgetItem(self._format(diagnostic))
            item.setForeground(self._color(str(diagnostic.severity.value)))
            self.addItem(item)
        if not diagnostics:
            from PySide6.QtGui import QColor

            item = QListWidgetItem("No diagnostics.")
            item.setForeground(QColor(theme.INFO))
            self.addItem(item)

    @staticmethod
    def _format(diagnostic: Any) -> str:
        return f"{diagnostic.code}: {diagnostic.message}"

    @staticmethod
    def _color(severity_value: str):
        from PySide6.QtGui import QColor

        mapping = {
            "error": theme.DANGER,
            "critical": theme.DANGER,
            "warning": theme.WARNING,
            "info": theme.INFO,
        }
        return QColor(mapping.get(severity_value, theme.TEXT))


__all__ = ["DiagnosticsView"]
