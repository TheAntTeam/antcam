"""Background geometry/fixture import on the global thread pool.

Importing is read-only against the file system and pure functions, so it runs
safely off the main thread. The worker captures the path on the main thread
before starting; results are delivered back to the UI through a queued signal
carrier, so the UI stays responsive.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from antcam_rc2.app.application import Application
from antcam_rc2.core.geometry3d.scene import SolidScene
from antcam_rc2.core.io import import_file
from antcam_rc2.core.io.scene import GeometryScene
from antcam_rc2.core.io3d import import_file_3d
from antcam_rc2.core.project.fixture_library import FixtureLibrary
from antcam_rc2.core.project.models import Fixture


class _SignalCarrier(QObject):
    """Thread-safe delivery of worker results to the main thread."""

    geometry_finished = Signal(object)  # GeometryScene
    solid_finished = Signal(object)  # SolidScene
    fixture_finished = Signal(object)  # Fixture
    failed = Signal(str)


class _Import2DRunnable(QRunnable):
    """Runs 2D geometry import (DXF/SVG) in background."""

    def __init__(
        self,
        path: Path,
        carrier: _SignalCarrier,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._carrier = carrier

    def run(self) -> None:
        try:
            scene = import_file(self._path)
        except Exception as exc:  # noqa: BLE001 - reported to the UI as a failure
            self._carrier.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self._carrier.geometry_finished.emit(scene)


class _Import3DRunnable(QRunnable):
    """Runs 3D solid import (STEP/STL) in background."""

    def __init__(
        self,
        path: Path,
        carrier: _SignalCarrier,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._carrier = carrier

    def run(self) -> None:
        try:
            scene = import_file_3d(self._path)
        except Exception as exc:  # noqa: BLE001 - reported to the UI as a failure
            self._carrier.failed.emit(f"{type(exc).__name__}: {exc}")
            return

        self._carrier.solid_finished.emit(scene)


class _LoadFixtureRunnable(QRunnable):
    """Runs fixture loading from library (including mesh if present) in background."""

    def __init__(
        self,
        fixture_id: str,
        carrier: _SignalCarrier,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._fixture_id = fixture_id
        self._carrier = carrier

    def run(self) -> None:
        try:
            lib = FixtureLibrary.get_default()
            fixture = lib.get_fixture(self._fixture_id)
            if fixture is None:
                self._carrier.failed.emit(f"Fixture not found in library: {self._fixture_id}")
                return
            # If fixture has a mesh_path, load the mesh into the fixture
            if fixture.mesh_path:
                mesh_path = lib.base_dir / fixture.mesh_path
                if mesh_path.exists():
                    # Load the mesh to populate features - this is the expensive part
                    from antcam_rc2.core.io3d import import_file_3d

                    import_file_3d(mesh_path)
                    # Fixture already has mesh_path set; we just need to ensure it's valid
                    # The actual mesh loading for rendering happens in setup_to_scene
        except Exception as exc:  # noqa: BLE001 - reported to the UI as a failure
            self._carrier.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self._carrier.fixture_finished.emit(fixture)


class GeometryImportController(QObject):
    """Owns import tasks on the global pool and exposes UI-facing signals."""

    busy_changed = Signal(bool)
    geometry_imported = Signal(object)  # GeometryScene
    solid_imported = Signal(object)  # SolidScene
    fixture_loaded = Signal(object)  # Fixture
    import_failed = Signal(str)

    def __init__(self, application: Application, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._application = application
        self._pool = QThreadPool.globalInstance()
        self._busy = False

    @property
    def is_busy(self) -> bool:
        return self._busy

    def import_geometry(self, path: Path) -> None:
        """Start background 2D geometry import; emits ``geometry_imported`` or ``import_failed``."""
        if self._busy:
            return
        self._busy = True
        self.busy_changed.emit(True)

        carrier = _SignalCarrier(self)
        carrier.geometry_finished.connect(self._on_geometry_finished)
        carrier.failed.connect(self._on_failed)
        runnable = _Import2DRunnable(path, carrier)
        self._pool.start(runnable)

    def import_solid(self, path: Path) -> None:
        """Start background 3D solid import; emits ``solid_imported`` or ``import_failed``."""
        if self._busy:
            return
        self._busy = True
        self.busy_changed.emit(True)

        carrier = _SignalCarrier(self)
        carrier.solid_finished.connect(self._on_solid_finished)
        carrier.failed.connect(self._on_failed)
        runnable = _Import3DRunnable(path, carrier)
        self._pool.start(runnable)

    def load_fixture(self, fixture_id: str) -> None:
        """Start background fixture loading; emits ``fixture_loaded`` or ``import_failed``."""
        if self._busy:
            return
        self._busy = True
        self.busy_changed.emit(True)

        carrier = _SignalCarrier(self)
        carrier.fixture_finished.connect(self._on_fixture_finished)
        carrier.failed.connect(self._on_failed)
        runnable = _LoadFixtureRunnable(fixture_id, carrier)
        self._pool.start(runnable)

    def cancel(self) -> None:
        """Request the UI to stop waiting (the in-flight import completes or fails)."""
        self._busy = False
        self.busy_changed.emit(False)

    def _on_geometry_finished(self, scene: GeometryScene) -> None:
        self._busy = False
        self.busy_changed.emit(False)
        self.geometry_imported.emit(scene)

    def _on_solid_finished(self, scene: SolidScene) -> None:
        self._busy = False
        self.busy_changed.emit(False)
        self.solid_imported.emit(scene)

    def _on_fixture_finished(self, fixture: Fixture) -> None:
        self._busy = False
        self.busy_changed.emit(False)
        self.fixture_loaded.emit(fixture)

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.busy_changed.emit(False)
        self.import_failed.emit(message)
