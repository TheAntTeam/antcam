"""3D file import (STL/STEP): registry, importers and the public entry point."""

from __future__ import annotations

from antcam_rc2.core.geometry3d.scene import SolidScene, SolidSourceInfo
from antcam_rc2.core.io3d.registry import FormatRegistry3D, import_file_3d, registry_3d

__all__ = [
    "FormatRegistry3D",
    "SolidScene",
    "SolidSourceInfo",
    "import_file_3d",
    "registry_3d",
]
