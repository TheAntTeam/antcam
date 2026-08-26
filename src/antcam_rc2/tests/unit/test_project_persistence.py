"""Tests for stable versioned project-document persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from antcam_rc2.core.project.models import CatalogSnapshot, Project, Stock
from antcam_rc2.core.project.persistence import (
    PROJECT_DOCUMENT_SCHEMA_VERSION,
    ProjectDocument,
    dump_project_document,
    load_project_document,
    read_project_document,
    write_project_document,
)


def make_document() -> ProjectDocument:
    now = datetime.now(UTC)
    project = Project(
        id="proj_1234abcd",
        name="Persisted project",
        created_at=now,
        modified_at=now,
        machine_id="makera_z1",
        stock=Stock(width_mm=100.0, length_mm=80.0, height_mm=10.0, material_id="aluminum_6061"),
    )
    return ProjectDocument(
        schema_version=PROJECT_DOCUMENT_SCHEMA_VERSION,
        project=project,
        catalog_snapshot=CatalogSnapshot(
            versions={"machines": "1.0", "tools": "1.0", "materials": "1.0", "cooling": "1.0"}
        ),
    )


def test_project_document_round_trips_stably_and_writes_atomically(tmp_path: Path) -> None:
    document = make_document()
    destination = tmp_path / "fixture-plate.antcam.json"

    write_project_document(destination, document)

    assert read_project_document(destination) == document
    assert load_project_document(dump_project_document(document)) == document
    assert not list(tmp_path.glob("*.tmp"))


def test_project_document_rejects_unknown_schema_and_invalid_json() -> None:
    document = make_document()
    payload = document.model_dump(mode="json")
    payload["schema_version"] = "99.0"

    with pytest.raises(ValidationError, match="schema_version"):
        ProjectDocument.model_validate(payload)
    with pytest.raises(ValidationError):
        load_project_document("{ not JSON")
