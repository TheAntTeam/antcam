"""Background toolpath planning on the global thread pool.

Planning is read-only against immutable snapshots (``plan_snapshot``), so it
runs safely off the main thread.  The worker captures the project snapshot and
the transient scene on the main thread before starting; results are delivered
back to the UI through a queued signal carrier, so the UI stays responsive
without ever touching the kernel event bus from a worker thread.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from antcam_rc2.app.application import Application
from antcam_rc2.core.io.scene import GeometryScene
from antcam_rc2.core.project.models import Project
from antcam_rc2.core.toolpath.models import ToolpathPlan
from antcam_rc2.core.toolpath.settings import PlanningSettings

try:
    from antcam_rc2.core.geometry3d.scene import SolidScene
except ImportError:  # pragma: no cover
    SolidScene = object  # type: ignore[misc,assignment]


class _SignalCarrier(QObject):
    """Thread-safe delivery of worker results to the main thread."""

    finished = Signal(object)
    failed = Signal(str)


class _PlanRunnable(QRunnable):
    """Runs one ``plan_snapshot`` call; emits results through ``carrier``."""

    def __init__(
        self,
        application: Application,
        project: Project,
        scene: GeometryScene,
        settings: PlanningSettings,
        carrier: _SignalCarrier,
        solid_scene: SolidScene | None = None,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._application = application
        self._project = project
        self._scene = scene
        self._settings = settings
        self._carrier = carrier
        self._solid_scene = solid_scene

    def run(self) -> None:
        try:
            plan = self._application.toolpath_service.plan_snapshot(
                self._project, self._scene, self._settings, solid_scene=self._solid_scene
            )
        except Exception as exc:  # noqa: BLE001 - reported to the UI as a failure
            self._carrier.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self._carrier.finished.emit(plan)


class ToolpathController(QObject):
    """Owns planning tasks on the global pool and exposes UI-facing signals."""

    busy_changed = Signal(bool)
    plan_ready = Signal(object)
    plan_failed = Signal(str)

    def __init__(self, application: Application, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._application = application
        self._pool = QThreadPool.globalInstance()
        self._busy = False

    @property
    def is_busy(self) -> bool:
        return self._busy

    def generate(
        self,
        project_id: str,
        scene: GeometryScene,
        settings: PlanningSettings | None = None,
        solid_scene: SolidScene | None = None,
    ) -> None:
        """Start background planning; emits ``plan_ready`` or ``plan_failed``."""
        if self._busy:
            return
        project = self._application.project_service.get_project(project_id)
        settings = settings or PlanningSettings(clearance_z_mm=5.0)
        carrier = _SignalCarrier(self)
        carrier.finished.connect(self._on_finished)
        carrier.failed.connect(self._on_failed)
        runnable = _PlanRunnable(self._application, project, scene, settings, carrier, solid_scene)
        self._busy = True
        self.busy_changed.emit(True)
        self._pool.start(runnable)

    def cancel(self) -> None:
        """Request the UI to stop waiting (the in-flight plan completes or fails)."""
        self._busy = False
        self.busy_changed.emit(False)

    def _on_finished(self, plan: ToolpathPlan) -> None:
        self._busy = False
        self.busy_changed.emit(False)
        self.plan_ready.emit(plan)

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.busy_changed.emit(False)
        self.plan_failed.emit(message)
