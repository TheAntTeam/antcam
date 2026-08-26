"""Import diagnostics: warnings and errors collected while importing files."""

from __future__ import annotations

from pydantic import BaseModel

__all__ = ["ImportDiagnostics"]


class ImportDiagnostics(BaseModel):
    """Collects non-fatal issues found while parsing a file.

    Each entry carries a human-readable message and an optional reference to
    the source entity (e.g. ``"entity=12 layer=0"``) to ease debugging.
    """

    warnings: list[str] = []
    errors: list[str] = []

    @property
    def has_errors(self) -> bool:
        """True when at least one error was recorded (blocks validation)."""
        return bool(self.errors)

    def add_warning(self, message: str, *, entity: str | int | None = None) -> None:
        """Record a warning with an optional entity reference."""
        self.warnings.append(self._format(message, entity))

    def add_error(self, message: str, *, entity: str | int | None = None) -> None:
        """Record an error with an optional entity reference."""
        self.errors.append(self._format(message, entity))

    def summary(self) -> str:
        """A one-line textual summary for CLI output."""
        parts = [f"{len(self.warnings)} warning(s)", f"{len(self.errors)} error(s)"]
        return ", ".join(parts)

    @staticmethod
    def _format(message: str, entity: str | int | None) -> str:
        if entity is None:
            return message
        return f"{message} ({entity=})"

    def __bool__(self) -> bool:
        return self.has_errors
