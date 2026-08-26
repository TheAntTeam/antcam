"""PySide6 panels: project setup, operations, parameters and toolpath."""

from __future__ import annotations

from antcam_rc2.frontends.pyside.panels.fixture_library_panel import FixtureLibraryPanel
from antcam_rc2.frontends.pyside.panels.geometries_panel import GeometriesPanel
from antcam_rc2.frontends.pyside.panels.operations_panel import OperationsPanel
from antcam_rc2.frontends.pyside.panels.parameters_panel import ParametersPanel
from antcam_rc2.frontends.pyside.panels.project_panel import ProjectPanel
from antcam_rc2.frontends.pyside.panels.toolpath_panel import ToolpathPanel

__all__ = [
    "FixtureLibraryPanel",
    "GeometriesPanel",
    "OperationsPanel",
    "ParametersPanel",
    "ProjectPanel",
    "ToolpathPanel",
]
