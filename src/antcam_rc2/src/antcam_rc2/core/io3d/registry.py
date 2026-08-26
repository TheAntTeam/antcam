"""3D format registry and the public ``import_file_3d`` entry point.

Mirrors :mod:`antcam_rc2.core.io.registry`: importers register by extension
and the registry dispatches, converting failures into domain errors.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from antcam_rc2.core.errors import GeometryError, UnsupportedFormatError
from antcam_rc2.core.geometry3d.scene import SolidScene
from antcam_rc2.core.io3d.step import import_step
from antcam_rc2.core.io3d.stl import import_stl

__all__ = ["FormatRegistry3D", "import_file_3d", "registry_3d"]

Importer3D = Callable[[str | Path], SolidScene]


class FormatRegistry3D:
    """Maps file extensions (without dot, case-insensitive) to 3D importers."""

    def __init__(self) -> None:
        self._importers: dict[str, Importer3D] = {}

    def register(self, extension: str, importer: Importer3D) -> None:
        """Register an importer for ``extension`` (e.g. ``"step"``)."""
        self._importers[extension.lower()] = importer

    def extensions(self) -> list[str]:
        """All registered extensions, sorted."""
        return sorted(self._importers)

    def import_file(self, path: str | Path) -> SolidScene:
        """Import a 3D file by extension.

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
                f"Unsupported 3D format: .{extension} (supported: {', '.join(self.extensions())})"
            )
        try:
            scene = importer(file_path)
        except GeometryError:
            raise
        except Exception as exc:
            raise GeometryError(f"Failed to import 3D file {file_path.name}: {exc}") from exc
        return scene


registry_3d = FormatRegistry3D()
registry_3d.register("stl", import_stl)
registry_3d.register("step", import_step)
registry_3d.register("stp", import_step)


def import_file_3d(path: str | Path) -> SolidScene:
    """Import a supported 3D file (STL/STEP) into a :class:`SolidScene`."""
    return registry_3d.import_file(path)
