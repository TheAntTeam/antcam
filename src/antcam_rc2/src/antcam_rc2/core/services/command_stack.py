"""Undo/redo stack for application commands."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from antcam_rc2.core.services.commands import Command


class CommandStack:
    """A bounded undo/redo stack.

    Pushing a new command clears the redo branch. The ``on_change`` callback
    (used by the UI) fires after every mutation.
    """

    def __init__(self, max_depth: int = 50) -> None:
        self._max_depth = max(1, max_depth)
        self._undo: deque[Command] = deque(maxlen=self._max_depth)
        self._redo: deque[Command] = deque()
        self._on_change: Callable[[], None] | None = None

    @property
    def max_depth(self) -> int:
        """Maximum number of undoable commands retained."""
        return self._max_depth

    def push(self, command: Command) -> None:
        """Execute ``command`` and push it onto the undo stack."""
        command.execute()
        self._undo.append(command)
        self._redo.clear()
        self._notify()

    def undo(self) -> bool:
        """Undo the most recent command; return True if any was undone.

        If the command's :meth:`~Command.undo` raises, the command is pushed
        back onto the undo stack so the failure does not corrupt history.
        """
        if not self._undo:
            return False
        command = self._undo.pop()
        try:
            command.undo()
        except Exception:
            self._undo.append(command)
            raise
        self._redo.append(command)
        self._notify()
        return True

    def redo(self) -> bool:
        """Redo the most recently undone command; return True if any was redone.

        If the command's :meth:`~Command.execute` raises, the command is pushed
        back onto the redo stack so the failure does not corrupt history.
        """
        if not self._redo:
            return False
        command = self._redo.pop()
        try:
            command.execute()
        except Exception:
            self._redo.append(command)
            raise
        self._undo.append(command)
        self._notify()
        return True

    def clear(self) -> None:
        """Drop all history."""
        self._undo.clear()
        self._redo.clear()
        self._notify()

    @property
    def can_undo(self) -> bool:
        """Whether an undo is available."""
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        """Whether a redo is available."""
        return bool(self._redo)

    @property
    def undo_count(self) -> int:
        """Number of commands on the undo stack."""
        return len(self._undo)

    @property
    def redo_count(self) -> int:
        """Number of commands on the redo stack."""
        return len(self._redo)

    def set_on_change(self, callback: Callable[[], None] | None) -> None:
        """Install or clear the change notification callback."""
        self._on_change = callback

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()
