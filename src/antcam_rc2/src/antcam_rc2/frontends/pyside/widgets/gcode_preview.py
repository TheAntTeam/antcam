"""Plain-text G-code preview with program statistics."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from antcam_rc2.core.post import GCodeProgram


class GCodePreview(QWidget):
    """Shows the first lines of a G-code program plus compact statistics."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._stats = QLabel("No program posted.", self)
        self._stats.setWordWrap(True)
        self._editor = QPlainTextEdit(self)
        self._editor.setReadOnly(True)
        self._editor.setMaximumBlockCount(40)
        layout.addWidget(self._stats)
        layout.addWidget(self._editor)

    def set_program(self, program: GCodeProgram) -> None:
        """Show the program header and update the statistics."""
        self._stats.setText(
            f"post {program.post_id} · {len(program.lines)} lines · {program.motion_line_count} motion · "
            f"fp {program.fingerprint()[:12]}"
        )
        self._editor.setPlainText("\n".join(program.lines[:40]))

    def clear(self) -> None:
        self._stats.setText("No program posted.")
        self._editor.clear()


__all__ = ["GCodePreview"]
