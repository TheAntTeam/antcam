"""Command pattern primitives for undoable operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Command(ABC):
    """A single, named, undoable unit of work.

    Subclasses implement :meth:`execute` and :meth:`undo`. ``undo`` may raise
    if the command was never executed or cannot be reverted.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable command name (shown in the undo history UI)."""

    @abstractmethod
    def execute(self) -> Any:
        """Apply the command and return an optional result."""

    @abstractmethod
    def undo(self) -> None:
        """Revert the effects of :meth:`execute`."""
