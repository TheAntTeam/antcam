"""Favorite operation templates persisted in QSettings (UI-scoped).

Favorites never touch the project document, preserving the stable
``.antcam.json`` schema.  They are reusable presets: a favorite stores the
operation type, tool, cooling and parameters without a runtime id.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QSettings

from antcam_rc2.core.project.models import Operation

_GROUP = "favorites"
_VERSION_KEY = "version"
_VERSION = "1"


class FavoritesStore:
    """Read/write named operation templates through QSettings."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings()

    def save(self, name: str, operation: Operation) -> None:
        """Store ``operation`` under a user-provided ``name``."""
        values = operation.model_dump(mode="json")
        payload = {
            key: values[key]
            for key in ("name", "operation_type", "tool_id", "cooling_id", "parameters")
            if key in values
        }
        self._settings.beginGroup(_GROUP)
        self._settings.setValue(_VERSION_KEY, _VERSION)
        self._settings.setValue(name, json.dumps(payload, ensure_ascii=True))
        self._settings.endGroup()

    def names(self) -> list[str]:
        """All favorite names in stable order."""
        self._settings.beginGroup(_GROUP)
        names = [key for key in self._settings.childKeys() if key != _VERSION_KEY]
        self._settings.endGroup()
        return sorted(names)

    def load(self, name: str) -> dict | None:
        """Return the template payload for ``name``, or ``None``."""
        self._settings.beginGroup(_GROUP)
        raw = self._settings.value(name, None)
        self._settings.endGroup()
        if raw is None:
            return None
        try:
            return json.loads(str(raw))
        except json.JSONDecodeError:
            return None

    def remove(self, name: str) -> None:
        self._settings.beginGroup(_GROUP)
        self._settings.remove(name)
        self._settings.endGroup()


__all__ = ["FavoritesStore"]
