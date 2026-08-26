"""2D file import: registry, scene model, diagnostics and importers.

The public entry point is :func:`import_file`, which returns a
:class:`GeometryScene` for any supported format (currently DXF and SVG).
"""

from __future__ import annotations

from antcam_rc2.core.errors import GeometryError, UnsupportedFormatError
from antcam_rc2.core.io.diagnostics import ImportDiagnostics
from antcam_rc2.core.io.registry import FormatRegistry, import_file, registry
from antcam_rc2.core.io.scene import GeometryScene, SceneLayer, SourceInfo

__all__ = [
    "FormatRegistry",
    "GeometryError",
    "GeometryScene",
    "ImportDiagnostics",
    "SceneLayer",
    "SourceInfo",
    "UnsupportedFormatError",
    "import_file",
    "registry",
]
