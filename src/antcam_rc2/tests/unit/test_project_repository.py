"""Tests for the in-memory immutable project repository."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from antcam_rc2.core.errors import ProjectError
from antcam_rc2.core.project.models import Project, Stock
from antcam_rc2.core.project.repository import InMemoryProjectRepository


def make_project(project_id: str = "proj_1234abcd") -> Project:
    now = datetime.now(UTC)
    return Project(
        id=project_id,
        name="Test project",
        created_at=now,
        modified_at=now,
        machine_id="makera_z1",
        stock=Stock(width_mm=100.0, length_mm=80.0, height_mm=10.0, material_id="aluminum_6061"),
    )


def test_repository_stores_replaces_and_removes_immutable_snapshots() -> None:
    repository = InMemoryProjectRepository()
    original = make_project()
    repository.add(original)

    replacement = original.model_copy(update={"name": "Renamed"})
    repository.replace(replacement)

    assert repository.get(original.id) == replacement
    assert repository.list_ids() == (original.id,)
    repository.remove(original.id)
    assert repository.list_ids() == ()


def test_repository_rejects_duplicate_or_unknown_projects() -> None:
    repository = InMemoryProjectRepository()
    project = make_project()
    repository.add(project)

    with pytest.raises(ProjectError, match="already exists"):
        repository.add(project)
    with pytest.raises(ProjectError, match="not found"):
        repository.get("proj_deadbeef")
    with pytest.raises(ProjectError, match="not found"):
        repository.replace(make_project("proj_deadbeef"))
