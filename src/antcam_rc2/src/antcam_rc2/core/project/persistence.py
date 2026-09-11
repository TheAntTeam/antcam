"""Versioned, atomic JSON persistence for project setup documents."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field

from antcam_rc2.core.errors import ProjectError
from antcam_rc2.core.project.models import CatalogSnapshot, Project, _ProjectModel
from antcam_rc2.core.project.stock_geometry import stock_min_corner

PROJECT_DOCUMENT_SCHEMA_VERSION = "1.1"


class ProjectDocument(_ProjectModel):
    """The complete portable JSON envelope for one project setup."""

    schema_version: Literal["1.1"] = PROJECT_DOCUMENT_SCHEMA_VERSION
    project: Project
    catalog_snapshot: CatalogSnapshot
    notes: str = Field(default="")


def dump_project_document(document: ProjectDocument) -> str:
    """Return deterministic human-readable JSON for one validated document."""
    return json.dumps(document.model_dump(mode="json"), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def load_project_document(payload: str | bytes) -> ProjectDocument:
    """Parse and validate one project document from JSON text."""
    document = ProjectDocument.model_validate_json(payload)
    return migrate_document(document)


def migrate_document(document: ProjectDocument) -> ProjectDocument:
    """Migrate project document from older schema versions to current.

    Schema 1.0 -> 1.1: Fixture positions were absolute WCS coordinates.
    Now they are offsets from stock minimum corner (left, front, bottom).
    """
    if document.schema_version == "1.1":
        return document

    # Schema 1.0 migration
    if document.schema_version == "1.0":
        project = document.project
        if project.fixtures:
            stock = project.stock
            wcs = project.wcs
            stock_min_x, stock_min_y, stock_min_z = stock_min_corner(stock, wcs)

            migrated_fixtures = []
            for fixture in project.fixtures:
                # Old positions were absolute; convert to offset from stock corner
                offset_x = fixture.position_x_mm - stock_min_x
                offset_y = fixture.position_y_mm - stock_min_y
                offset_z = fixture.position_z_mm - stock_min_z
                migrated = fixture.model_copy(
                    update={
                        "position_x_mm": offset_x,
                        "position_y_mm": offset_y,
                        "position_z_mm": offset_z,
                    }
                )
                migrated_fixtures.append(migrated)

            project = project.model_copy(update={"fixtures": tuple(migrated_fixtures)})
            document = document.model_copy(update={"project": project, "schema_version": "1.1"})

    return document


def write_project_document(destination: Path, document: ProjectDocument) -> None:
    """Atomically write a project document beside its final destination."""
    if not destination.parent.is_dir():
        raise ProjectError(f"project directory does not exist: {destination.parent}")
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(dump_project_document(document))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
    except OSError as exc:
        raise ProjectError(f"failed to write project document: {destination}") from exc
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def read_project_document(source: Path) -> ProjectDocument:
    """Read and validate a project document from a UTF-8 JSON file."""
    try:
        payload = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProjectError(f"failed to read project document: {source}") from exc
    return load_project_document(payload)
