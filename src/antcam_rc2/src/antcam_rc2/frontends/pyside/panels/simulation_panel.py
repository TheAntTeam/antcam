"""Simulation panel: run, timeline playback and collision report."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from antcam_rc2.core.simulation import SimulationSettings
from antcam_rc2.core.simulation.simulator import SimulationResult
from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController
from antcam_rc2.frontends.pyside.widgets.diagnostics_view import DiagnosticsView
from antcam_rc2.frontends.pyside.widgets.timeline_widget import TimelineWidget


class SimulationPanel(QWidget):
    """Runs the voxel simulation in the background and plays it back."""

    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._result: SimulationResult | None = None
        self._playing = False
        self._active = False
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(40)
        self._play_timer.timeout.connect(self._on_play_tick)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self._run_button = QPushButton("Simulate", self)
        self._run_button.setObjectName("primary")
        controls.addWidget(self._run_button)
        controls.addWidget(QLabel("Voxel (mm):", self))
        self._resolution = QDoubleSpinBox(self)
        self._resolution.setRange(0.1, 10.0)
        self._resolution.setDecimals(2)
        self._resolution.setValue(1.0)
        self._resolution.setEnabled(False)
        controls.addWidget(self._resolution)
        controls.addStretch(1)
        layout.addLayout(controls)

        self._timeline = TimelineWidget(self)
        layout.addWidget(self._timeline)

        self._stats = QLabel("No simulation run.", self)
        self._stats.setWordWrap(True)
        layout.addWidget(self._stats)

        self._events = DiagnosticsView(self)
        layout.addWidget(self._events)

        self._run_button.clicked.connect(self._on_run)
        self._timeline.play_requested.connect(self._on_play)
        self._timeline.pause_requested.connect(self._on_pause)
        self._timeline.step_forward_requested.connect(lambda: self._seek(1))
        self._timeline.step_backward_requested.connect(lambda: self._seek(-1))
        self._timeline.reset_requested.connect(self._on_reset)
        self._timeline.tick_selected.connect(self._on_tick_selected)
        self._controller.toolpath_controller.busy_changed.connect(self._on_busy)
        # Re-enable "Simulate" as soon as a toolpath is generated/cleared; without
        # this the button stayed disabled until the project was reopened.
        self._controller.toolpath_changed.connect(self.refresh)
        # Connect the simulation worker once (not on every run, which stacked
        # duplicate callbacks and voxel updates).
        simulation_controller = self._controller.simulation_controller
        simulation_controller.progress.connect(self._on_progress)
        simulation_controller.simulation_finished.connect(self._on_finished)
        simulation_controller.failed.connect(self._on_failed)
        self.refresh()  # start with "Simulate" disabled until a plan exists

    def refresh(self) -> None:
        """Re-enable the run button when a plan is available."""
        plan = self._controller.last_plan
        self._run_button.setEnabled(plan is not None and plan.is_executable)
        if self._active:
            self.enter()

    def enter(self) -> None:
        """Called when the Simulation tab becomes visible.

        Shows the untouched stock as the opaque voxel block so the user always
        sees the material that the toolpath will carve.
        """
        self._active = True
        if self._controller.project is None:
            return
        if self._result is None:
            self._show_full_stock()
        else:
            self._show_mask_at(self._timeline.current_tick())

    def leave(self) -> None:
        """Called when another tab becomes visible: restore the normal view."""
        if not self._active:
            return
        self._active = False
        self._controller.clear_simulation()

    def _show_full_stock(self) -> None:
        """Render the untouched stock as the opaque voxel block."""
        project = self._controller.project
        if project is None:
            return
        from antcam_rc2.core.simulation.voxels import VoxelGrid

        grid, resolution, _clamped = VoxelGrid.from_stock(project.stock, project.wcs, self._resolution.value())
        triangles = self._controller.update_simulation_view(grid.occupied, resolution, None, 0)
        self._stats.setText(
            f"Stock block shown: {triangles} triangles at {resolution:.2f} mm voxels."
            if triangles
            else "Simulation view unavailable (no stock mesh)."
        )
        self._controller.status_message.emit(
            f"Simulation view: stock block {triangles} triangles at {resolution:.2f} mm voxels"
            if triangles
            else "Simulation view: no stock mesh to show"
        )

    # ------------------------------------------------------------------ actions
    def _on_run(self) -> None:
        plan = self._controller.last_plan
        project = self._controller.project
        if plan is None or project is None:
            return
        settings = SimulationSettings(voxel_resolution_mm=self._resolution.value())
        self._events.set_diagnostics(())
        self._controller.simulation_controller.simulate(project, plan, settings)

    def _on_failed(self, message: str) -> None:
        self._stats.setText(f"Simulation failed: {message}")

    def _on_progress(self, payload) -> None:
        motion_index, position, removed_mm3, downsampled, tool_id = payload
        if downsampled is not None:
            # Live mesh preview: the mask is downsampled x2, so the voxel size doubles.
            self._controller.update_simulation_view(downsampled, self._resolution.value() * 2.0, position, -1, tool_id)
        else:
            self._controller.update_simulation_view(None, 0.0, position, -1, tool_id)
        self._stats.setText(
            f"Simulating... {self._tool_label(tool_id)}  tick {motion_index}  removed {removed_mm3:.0f} mm³"
        )

    def _tool_label(self, tool_id: str | None) -> str:
        if not tool_id:
            return "tool"
        try:
            return self._controller.services.catalog_repository.tool(tool_id).name
        except Exception:  # noqa: BLE001 - unknown tool id
            return tool_id

    def _on_finished(self, result: SimulationResult) -> None:
        self._result = result
        report = result.report
        last_tick = max(0, len(report.timeline) - 1)
        self._timeline.set_range(last_tick)
        # Show the finished (fully carved) state immediately, not the untouched
        # stock: the user wants to see what the toolpath removed.
        self._timeline.set_tick(last_tick)
        events = list(report.events)
        self._events.set_diagnostics(events)
        stats = report.stats
        self._stats.setText(
            f"Removed {stats.removed_mm3:.0f} mm³ · max depth {stats.max_depth_reached_mm:.2f} mm · "
            f"collisions {stats.collision_count} · {stats.operations_simulated} ops · "
            f"est. {stats.duration_estimate_s:.0f} s"
        )
        self._show_mask_at(last_tick)

    # ------------------------------------------------------------------ playback
    def _on_play(self) -> None:
        # Replay from the start when the timeline is at the end.
        if self._timeline.current_tick() >= self._timeline._slider.maximum():
            self._timeline.set_tick(0)
            self._show_mask_at(0)
        self._playing = True
        self._timeline.set_playing(True)
        self._play_timer.start()

    def _on_pause(self) -> None:
        self._playing = False
        self._timeline.set_playing(False)
        self._play_timer.stop()

    def _on_play_tick(self) -> None:
        self._seek(1)
        if self._timeline.current_tick() >= self._timeline._slider.maximum():
            self._on_pause()

    def _seek(self, delta: int) -> None:
        tick = max(0, self._timeline.current_tick() + delta)
        self._timeline.set_tick(tick)
        self._show_mask_at(tick)

    def _on_reset(self) -> None:
        self._on_pause()
        self._result = None
        self._timeline.set_range(0)
        self._timeline.set_tick(0)
        self._events.set_diagnostics(())
        self._stats.setText("No simulation run.")
        self._controller.clear_simulation()
        # Back to the untouched stock while the Simulation tab is visible.
        if self._active:
            self._show_full_stock()

    def _on_tick_selected(self, tick: int) -> None:
        self._show_mask_at(tick)

    # ------------------------------------------------------------------ viewport
    def _show_mask_at(self, tick: int) -> None:
        if self._result is None:
            return
        sim = self._controller.simulation_controller
        mask = sim.mask_at_tick(tick)
        if mask is None:
            return
        tick_data = self._result.report.timeline[min(tick, len(self._result.report.timeline) - 1)]
        self._controller.update_simulation_view(
            mask,
            self._result.report.settings.voxel_resolution_mm or 1.0,
            (tick_data.tool_position.x_mm, tick_data.tool_position.y_mm, tick_data.tool_position.z_mm),
            tick,
            self._tool_id_for_motion(tick_data.motion_index),
        )

    def _tool_id_for_motion(self, motion_index: int) -> str | None:
        """The tool of the plan operation that owns ``motion_index``."""
        plan = self._controller.last_plan
        project = self._controller.project
        if plan is None or project is None:
            return None
        flat = 0
        for result in plan.operations:
            program = result.program
            if program is None:
                continue
            if motion_index < flat + len(program.motions):
                operation = next((op for op in project.operations if op.id == result.operation_id), None)
                return operation.tool_id if operation is not None else None
            flat += len(program.motions)
        return None

    def _update_marker(self, position: tuple[float, float, float], downsampled) -> None:
        del downsampled
        self._controller.update_simulation_view(None, 0.0, position, -1)

    def _on_busy(self, busy: bool) -> None:
        self._resolution.setEnabled(not busy)
