"""Toolpath panel: generate, diagnostics, post-processing and artifact export."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from antcam_rc2.core.post import PostService
from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController
from antcam_rc2.frontends.pyside.widgets.diagnostics_view import DiagnosticsView
from antcam_rc2.frontends.pyside.widgets.gcode_preview import GCodePreview


class ToolpathPanel(QWidget):
    """Generates the toolpath in the background and shows its diagnostics."""

    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        self._generate_button = QPushButton("Generate toolpath", self)
        self._generate_button.setObjectName("primary")
        self._export_button = QPushButton("Export artifact", self)
        buttons.addWidget(self._generate_button)
        buttons.addWidget(self._export_button)
        layout.addLayout(buttons)
        self._status = QLabel("No toolpath generated.", self)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._diagnostics = DiagnosticsView(self)
        layout.addWidget(self._diagnostics)

        post_row = QHBoxLayout()
        post_row.addWidget(QLabel("Post:", self))
        self._post_combo = QComboBox(self)
        self._post_combo.addItems(list(PostService(controller.services.catalog_repository).post_ids()))
        post_row.addWidget(self._post_combo)
        self._export_nc_button = QPushButton("Export .nc", self)
        self._export_nc_button.setObjectName("primary")
        post_row.addWidget(self._export_nc_button)
        post_row.addStretch(1)
        layout.addLayout(post_row)
        self._preview = GCodePreview(self)
        layout.addWidget(self._preview)

        self._generate_button.clicked.connect(self._on_generate)
        self._export_button.clicked.connect(self._on_export)
        self._export_nc_button.clicked.connect(self._on_export_nc)
        self._controller.toolpath_controller.busy_changed.connect(self._on_busy)
        self._export_nc_button.setEnabled(False)

    def refresh(self) -> None:
        """Update the diagnostics view from the last planning result."""
        plan = self._controller.last_plan
        if plan is None:
            self._status.setText("No toolpath generated.")
            self._diagnostics.set_diagnostics(())
            self._export_nc_button.setEnabled(False)
            return
        diagnostics = list(plan.diagnostics)
        for result in plan.operations:
            diagnostics.extend(result.diagnostics)
        self._diagnostics.set_diagnostics(diagnostics)
        if plan.is_executable:
            self._status.setText(f"Toolpath executable: {len(plan.operations)} operations planned.")
            self._export_nc_button.setEnabled(True)
        else:
            self._status.setText("Toolpath has errors — not executable.")
            self._export_nc_button.setEnabled(False)

    def _on_busy(self, busy: bool) -> None:
        self._generate_button.setEnabled(not busy)
        if busy:
            self._status.setText("Planning toolpath in background...")

    def _on_generate(self) -> None:
        self._controller.generate_toolpath()

    def _on_export(self) -> None:
        plan = self._controller.last_plan
        project = self._controller.project
        scene = self._controller.scene
        if plan is None or project is None or scene is None:
            return
        if not plan.is_executable:
            self._status.setText("Cannot export a non-executable plan.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export toolpath artifact", "plan.toolpath.json", "Toolpath artifact (*.toolpath.json)"
        )
        if path:
            from antcam_rc2.core.toolpath.settings import PlanningSettings

            settings = plan.settings or PlanningSettings(clearance_z_mm=5.0)
            artifact = self._controller.services.toolpath_service.export_artifact(project.id, scene, settings)
            Path(path).write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
            self._status.setText(f"Artifact exported to {Path(path).name}")

    def _on_export_nc(self) -> None:
        project = self._controller.project
        plan = self._controller.last_plan
        if project is None or plan is None or not plan.is_executable:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export G-code", "output.nc", "G-code (*.nc *.gcode)")
        if not path:
            return
        service = PostService(self._controller.services.catalog_repository)
        try:
            program = service.post(project, plan, post_id=self._post_combo.currentText())
        except Exception as exc:  # noqa: BLE001 - reported to the status line
            self._status.setText(f"Post failed: {exc}")
            return
        Path(path).write_text(program.text() + "\n", encoding="utf-8")
        self._preview.set_program(program)
        self._status.setText(f"G-code exported to {Path(path).name} ({len(program.lines)} lines)")
