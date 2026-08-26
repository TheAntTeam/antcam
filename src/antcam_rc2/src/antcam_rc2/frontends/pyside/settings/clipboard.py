"""UI-scoped operation clipboard: JSON round-trip of one operation.

Copy/paste lives entirely in the frontend: the payload is a serializable
``Operation``-like dict and pasting always goes through
``ProjectService.add_operation`` so catalog validation and a fresh runtime id
are guaranteed.
"""

from __future__ import annotations

import json

from antcam_rc2.core.project.models import Operation

_PAYLOAD_KEYS = ("name", "operation_type", "tool_id", "cooling_id", "parameters")


class _OperationClipboard:
    """Holds one serialized operation for copy/paste within the session."""

    def __init__(self) -> None:
        self._payload: dict | None = None

    def copy(self, operation: Operation) -> None:
        """Store a canonical, JSON-safe copy of an operation (no runtime id)."""
        values = operation.model_dump(mode="json")
        self._payload = {key: values[key] for key in _PAYLOAD_KEYS if key in values}

    def paste(self) -> dict | None:
        """Return a deep copy of the stored payload, or ``None`` when empty."""
        if self._payload is None:
            return None
        return json.loads(json.dumps(self._payload))

    def clear(self) -> None:
        self._payload = None

    def has_payload(self) -> bool:
        return self._payload is not None


operation_clipboard = _OperationClipboard()

__all__ = ["operation_clipboard"]
