"""Format registry and the public ``import_file`` entry point.

Importer implementations register themselves by file extension.  The registry
dispatches on the suffix and converts low-level failures into the documented
domain errors.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from antcam_rc2.core.errors import GeometryError, UnsupportedFormatError
from antcam_rc2.core.io.dxf import import_dxf
from antcam_rc2.core.io.scene import GeometryScene
from antcam_rc2.core.io.svg import import_svg

__all__ = ["FormatRegistry", "import_file", "registry"]

Importer = Callable[[str | Path], GeometryScene]


class FormatRegistry:
    """Maps file extensions (without dot, case-insensitive) to importers."""

    def __init__(self) -> None:
        self._importers: dict[str, Importer] = {}

    def register(self, extension: str, importer: Importer) -> None:
        """Register an importer for ``extension`` (e.g. ``"dxf"``)."""
        self._importers[extension.lower()] = importer

    def extensions(self) -> list[str]:
        """All registered extensions, sorted."""
        return sorted(self._importers)

    def import_file(self, path: str | Path) -> GeometryScene:
        """Import a file by extension.

        Raises:
            FileNotFoundError: if the file does not exist.
            UnsupportedFormatError: if the extension has no importer.
            GeometryError: if the importer fails on the content.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(str(file_path))
        extension = file_path.suffix.lower().lstrip(".")
        importer = self._importers.get(extension)
        if importer is None:
            raise UnsupportedFormatError(
                f"Unsupported format: .{extension} (supported: {', '.join(self.extensions())})"
            )
        try:
            scene = importer(file_path)
        except GeometryError:
            raise
        except Exception as exc:
            raise GeometryError(f"Failed to import {file_path.name}: {exc}") from exc
        return scene


registry = FormatRegistry()
registry.register("dxf", import_dxf)
registry.register("svg", import_svg)


def import_file(path: str | Path) -> GeometryScene:
    """Import a supported 2D file into a :class:`GeometryScene`.

    Convenience wrapper over the shared :data:`registry`.
    """
    return registry.import_file(path)
