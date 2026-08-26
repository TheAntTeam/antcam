"""Snapshot commands for exact undo/redo of persistent project mutations."""

from __future__ import annotations

from dataclasses import dataclass

from antcam_rc2.core.project.models import Project
from antcam_rc2.core.project.repository import ProjectRepository
from antcam_rc2.core.services.commands import Command


@dataclass(slots=True)
class ReplaceProjectCommand(Command):
    """Replace one project snapshot and restore it exactly on undo."""

    repository: ProjectRepository
    before: Project
    after: Project
    label: str

    @property
    def name(self) -> str:
        return self.label

    def execute(self) -> None:
        self.repository.replace(self.after)

    def undo(self) -> None:
        self.repository.replace(self.before)
