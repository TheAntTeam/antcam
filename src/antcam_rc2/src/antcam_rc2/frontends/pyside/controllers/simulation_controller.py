"""Background simulation worker with live progress and checkpoint scrubbing.

The worker runs the deterministic simulator on the global thread pool (same
pattern as toolpath planning).  During the run it forwards periodic progress
(marker position, removed volume, downsampled occupancy for display meshes);
on completion it delivers the report plus the mask checkpoints, and exposes
``mask_at_tick`` for timeline scrubbing via checkpoint restore + sweep replay.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from antcam_rc2.app.application import Application
from antcam_rc2.core.project.models import Project
from antcam_rc2.core.simulation import (
    SimulationSettings,
    Simulator,
)
from antcam_rc2.core.simulation.simulator import SimulationResult
from antcam_rc2.core.toolpath.models import Position3, ToolpathPlan


class _SimulationCarrier(QObject):
    """Thread-safe delivery of worker events to the main thread."""

    progress = Signal(object)  # (motion_index, position, removed_mm3, downsampled_mask|None)
    finished = Signal(object)  # SimulationResult
    failed = Signal(str)


class _SimulationRunnable(QRunnable):
    """Runs one full simulation, emitting progress along the way."""

    def __init__(
        self,
        simulator: Simulator,
        project: Project,
        plan: ToolpathPlan,
        settings: SimulationSettings,
        carrier: _SimulationCarrier,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._simulator = simulator
        self._project = project
        self._plan = plan
        self._settings = settings
        self._carrier = carrier

    def run(self) -> None:
        def on_progress(
            motion_index: int, position: Position3, removed_voxels: int, occupancy: np.ndarray | None
        ) -> None:
            removed_mm3 = removed_voxels * (self._settings.voxel_resolution_mm or 1.0) ** 3
            downsampled = occupancy[::2, ::2, ::2] if occupancy is not None else None
            self._carrier.progress.emit(
                (motion_index, (position.x_mm, position.y_mm, position.z_mm), removed_mm3, downsampled)
            )

        try:
            result = self._simulator.simulate(
                self._project,
                self._plan,
                self._settings,
                progress_callback=on_progress,
                progress_every=4,
                mesh_every=32,
            )
        except Exception as exc:  # noqa: BLE001 - reported to the UI as a failure
            self._carrier.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self._carrier.finished.emit(result)


class SimulationController(QObject):
    """Owns simulation tasks and exposes progress/results to the UI."""

    busy_changed = Signal(bool)
    progress = Signal(object)
    simulation_finished = Signal(object)
    failed = Signal(str)

    def __init__(self, application: Application, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._application = application
        self._pool = QThreadPool.globalInstance()
        self._busy = False
        self._result: SimulationResult | None = None
        self._project: Project | None = None
        self._plan: ToolpathPlan | None = None
        self._settings: SimulationSettings | None = None

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def result(self) -> SimulationResult | None:
        return self._result

    def simulate(
        self,
        project: Project,
        plan: ToolpathPlan,
        settings: SimulationSettings | None = None,
    ) -> None:
        """Start a background simulation; emits ``progress`` / ``simulation_finished``."""
        if self._busy:
            return
        settings = settings or SimulationSettings()
        self._project = project
        self._plan = plan
        self._settings = settings
        self._result = None

        carrier = _SimulationCarrier(self)
        carrier.progress.connect(self._on_progress)
        carrier.finished.connect(self._on_finished)
        carrier.failed.connect(self._on_failed)
        runnable = _SimulationRunnable(
            Simulator(self._application.catalog_repository), project, plan, settings, carrier
        )
        self._busy = True
        self.busy_changed.emit(True)
        self._pool.start(runnable)

    def mask_at_tick(self, tick_index: int) -> np.ndarray | None:
        """The occupancy mask at a timeline tick (checkpoint restore + replay)."""
        if self._result is None or self._project is None or self._plan is None or self._settings is None:
            return None
        timeline = self._result.report.timeline
        if not timeline:
            return None
        target = min(tick_index, timeline[-1].index)
        tick = next((tick for tick in timeline if tick.index >= target), timeline[-1])
        return Simulator(self._application.catalog_repository).replay_mask_at(
            self._project,
            self._plan,
            self._result.report.settings,
            self._result.checkpoints,
            tick.motion_index,
        )

    def _on_progress(self, payload) -> None:
        self.progress.emit(payload)

    def _on_finished(self, result: SimulationResult) -> None:
        self._result = result
        self._busy = False
        self.busy_changed.emit(False)
        self.simulation_finished.emit(result)

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.busy_changed.emit(False)
        self.failed.emit(message)
