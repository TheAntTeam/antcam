"""Integration tests for the PySide6 frontend stub."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from antcam_rc2.core.config import AppConfig


@pytest.fixture()
def offscreen_qt() -> Iterator[None]:
    """Force Qt to use the offscreen platform for headless testing."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield


def test_create_window(offscreen_qt, app_config: AppConfig) -> None:
    from PySide6.QtWidgets import QApplication

    from antcam_rc2.app.application import Application
    from antcam_rc2.frontends.pyside.main import create_window

    core = Application(config=app_config)
    try:
        _ = QApplication.instance() or QApplication([])
        window = create_window(core)
        assert window.windowTitle().startswith("AntCAM RC2")
        assert window.width() == 1280
        assert window.height() == 860
    finally:
        core.shutdown()


def test_frontends_package_importable_without_exec() -> None:
    import importlib

    module = importlib.import_module("antcam_rc2.frontends.pyside.main")
    assert callable(module.create_window)
    assert callable(module.main)
