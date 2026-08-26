"""Tests for the UI-scoped clipboard and favorites store (offscreen Qt)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def tmp_settings(tmp_path) -> None:
    from PySide6.QtCore import QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))


def make_operation():
    from antcam_rc2.core.project.models import Operation, OperationParameters, OperationType

    return Operation(
        id="op_1234abcd",
        name="Favorite pocket",
        operation_type=OperationType.POCKETING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        parameters=OperationParameters(depth_mm=2.0, stepover_mm=1.0),
    )


def test_clipboard_copy_paste_round_trip() -> None:
    from antcam_rc2.frontends.pyside.settings.clipboard import _OperationClipboard

    clipboard = _OperationClipboard()
    assert not clipboard.has_payload()
    clipboard.copy(make_operation())
    assert clipboard.has_payload()
    payload = clipboard.paste()
    assert payload["operation_type"] == "pocketing"
    assert payload["parameters"]["depth_mm"] == 2.0
    assert "id" not in payload  # runtime ids are never copied
    clipboard.clear()
    assert not clipboard.has_payload()
    assert clipboard.paste() is None


def test_clipboard_paste_returns_independent_copy() -> None:
    from antcam_rc2.frontends.pyside.settings.clipboard import _OperationClipboard

    clipboard = _OperationClipboard()
    clipboard.copy(make_operation())
    first = clipboard.paste()
    first["name"] = "mutated"
    second = clipboard.paste()
    assert second["name"] == "Favorite pocket"


def test_favorites_save_load_list_remove(tmp_settings) -> None:
    from PySide6.QtCore import QSettings

    from antcam_rc2.frontends.pyside.settings.favorites import FavoritesStore

    store = FavoritesStore(QSettings())
    store.save("pocket-3mm", make_operation())
    assert store.names() == ["pocket-3mm"]
    payload = store.load("pocket-3mm")
    assert payload is not None
    assert payload["tool_id"] == "end_mill_3_175_2f"
    assert store.load("missing") is None
    store.remove("pocket-3mm")
    assert store.names() == []


def test_favorites_persist_across_store_instances(tmp_settings) -> None:
    from PySide6.QtCore import QSettings

    from antcam_rc2.frontends.pyside.settings.favorites import FavoritesStore

    FavoritesStore(QSettings()).save("tpl", make_operation())
    second = FavoritesStore(QSettings())
    assert second.names() == ["tpl"]
