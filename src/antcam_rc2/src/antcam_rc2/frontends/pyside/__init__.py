"""PySide6 desktop frontend (default UI).

Importing this package is lazy: PySide6 is only required when a frontend
module is actually used, so the core CLI works without the optional ``gui``
extra.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from antcam_rc2.frontends.pyside.app_window import MainWindow
    from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController
    from antcam_rc2.frontends.pyside.main import create_window, main
    from antcam_rc2.frontends.pyside.theme import apply as apply_theme

__all__ = ["MainWindow", "ProjectController", "apply_theme", "create_window", "main"]

_LAZY = {
    "MainWindow": "antcam_rc2.frontends.pyside.app_window",
    "ProjectController": "antcam_rc2.frontends.pyside.controllers.project_controller",
    "create_window": "antcam_rc2.frontends.pyside.main",
    "main": "antcam_rc2.frontends.pyside.main",
    "apply_theme": "antcam_rc2.frontends.pyside.theme",
}


def __getattr__(name: str):
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(name)
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value
