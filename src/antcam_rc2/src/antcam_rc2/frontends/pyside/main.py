"""PySide6 desktop frontend entry point.

The PySide6 import is deferred so that the GUI extras remain optional and the
core package never depends on Qt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from antcam_rc2.app.application import Application
from antcam_rc2.frontends.pyside import theme
from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController

if TYPE_CHECKING:
    from antcam_rc2.frontends.pyside.app_window import MainWindow


def create_window(core: Application, *, title: str | None = None) -> MainWindow:
    """Create the main window bound to the given application core."""

    from antcam_rc2.frontends.pyside.app_window import MainWindow

    controller = ProjectController(core)
    window = MainWindow(controller)
    if title is not None:
        window.setWindowTitle(title)
    return window


def create_application():  # noqa: ANN201 - return type is QApplication (lazy Qt import)
    """Create the QApplication with the requested OpenGL surface format."""
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    format = QSurfaceFormat()
    format.setVersion(4, 5)
    format.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    format.setDepthBufferSize(24)
    format.setSamples(4)
    format.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(format)

    app = QApplication([])
    theme.apply(app)
    return app


def main() -> int:
    """Bootstrap the Qt application with the full main window."""
    from PySide6.QtWidgets import QApplication

    core = Application()
    try:
        app = QApplication.instance() or create_application()
        controller = ProjectController(core)
        from antcam_rc2.frontends.pyside.app_window import MainWindow

        window = MainWindow(controller)
        window.show()
        return app.exec()
    finally:
        core.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
