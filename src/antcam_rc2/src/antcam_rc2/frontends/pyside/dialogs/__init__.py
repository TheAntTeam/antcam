"""UI-scoped dialogs for project and operation creation."""

from __future__ import annotations

from antcam_rc2.frontends.pyside.dialogs.add_operation_dialog import AddOperationDialog
from antcam_rc2.frontends.pyside.dialogs.fixture_library_dialog import FixtureLibraryDialog
from antcam_rc2.frontends.pyside.dialogs.import_solid_dialog import ImportSolidDialog
from antcam_rc2.frontends.pyside.dialogs.new_project_dialog import NewProjectDialog

__all__ = ["AddOperationDialog", "FixtureLibraryDialog", "ImportSolidDialog", "NewProjectDialog"]
