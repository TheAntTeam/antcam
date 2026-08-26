"""UI-scoped settings helpers (clipboard and favorite templates)."""

from __future__ import annotations

from antcam_rc2.frontends.pyside.settings.clipboard import operation_clipboard
from antcam_rc2.frontends.pyside.settings.favorites import FavoritesStore

__all__ = ["FavoritesStore", "operation_clipboard"]
