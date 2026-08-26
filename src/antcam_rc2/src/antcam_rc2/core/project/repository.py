"""In-memory storage of immutable persistent project snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from antcam_rc2.core.errors import ProjectError
from antcam_rc2.core.project.models import Project


class ProjectRepository(Protocol):
    """Storage boundary for project snapshots owned by application services."""

    def add(self, project: Project) -> None:
        """Store a project that does not yet exist."""

    def get(self, project_id: str) -> Project:
        """Return a stored project or raise ``ProjectError``."""

    def replace(self, project: Project) -> None:
        """Replace an existing project with a new immutable snapshot."""

    def remove(self, project_id: str) -> None:
        """Remove one existing project."""

    def list_ids(self) -> tuple[str, ...]:
        """Return stored IDs in insertion order."""


class InMemoryProjectRepository:
    """Small deterministic repository for the single-project Phase 3 workflow."""

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}

    def add(self, project: Project) -> None:
        if project.id in self._projects:
            raise ProjectError(f"project already exists: {project.id}")
        self._projects[project.id] = project

    def get(self, project_id: str) -> Project:
        try:
            return self._projects[project_id]
        except KeyError as exc:
            raise ProjectError(f"project not found: {project_id}") from exc

    def replace(self, project: Project) -> None:
        if project.id not in self._projects:
            raise ProjectError(f"project not found: {project.id}")
        self._projects[project.id] = project

    def remove(self, project_id: str) -> None:
        if project_id not in self._projects:
            raise ProjectError(f"project not found: {project_id}")
        del self._projects[project_id]

    def list_ids(self) -> tuple[str, ...]:
        return tuple(self._projects)

    def values(self) -> Iterable[Project]:
        """Yield snapshots in insertion order for diagnostics and future UI use."""
        return self._projects.values()
