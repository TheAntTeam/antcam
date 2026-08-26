"""Timeline widget: play/pause/step controls and a scrub slider."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSlider, QVBoxLayout, QWidget


class TimelineWidget(QWidget):
    """Playback controls for the simulation timeline."""

    play_requested = Signal()
    pause_requested = Signal()
    step_forward_requested = Signal()
    step_backward_requested = Signal()
    reset_requested = Signal()
    tick_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._slider = QSlider(self)
        self._slider.setOrientation(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.valueChanged.connect(self._on_slider)

        buttons = QHBoxLayout()
        self._play = QPushButton("Play", self)
        self._step_back = QPushButton("<", self)
        self._step_fwd = QPushButton(">", self)
        self._reset = QPushButton("Reset", self)
        buttons.addWidget(self._step_back)
        buttons.addWidget(self._play)
        buttons.addWidget(self._step_fwd)
        buttons.addStretch(1)
        buttons.addWidget(self._reset)
        layout.addWidget(self._slider)
        layout.addLayout(buttons)

        self._play.clicked.connect(self._on_play)
        self._step_fwd.clicked.connect(self.step_forward_requested)
        self._step_back.clicked.connect(self.step_backward_requested)
        self._reset.clicked.connect(self.reset_requested)

    def set_range(self, maximum: int) -> None:
        """Set the number of ticks (``0`` disables scrubbing)."""
        self._slider.setRange(0, max(0, maximum))

    def set_tick(self, tick: int) -> None:
        """Move the slider without emitting ``tick_selected``."""
        self._slider.blockSignals(True)
        self._slider.setValue(max(0, min(tick, self._slider.maximum())))
        self._slider.blockSignals(False)

    def current_tick(self) -> int:
        return self._slider.value()

    def set_playing(self, playing: bool) -> None:
        self._play.setText("Pause" if playing else "Play")

    def _on_slider(self, value: int) -> None:
        self.tick_selected.emit(value)

    def _on_play(self) -> None:
        if self._play.text() == "Pause":
            self.pause_requested.emit()
        else:
            self.play_requested.emit()
