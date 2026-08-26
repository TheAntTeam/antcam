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

PROJECT_DOCUMENT_SCHEMA_VERSION = "1.0"


class ProjectDocument(_ProjectModel):
    """The complete portable JSON envelope for one project setup."""

    schema_version: Literal["1.0"] = PROJECT_DOCUMENT_SCHEMA_VERSION
    project: Project
    catalog_snapshot: CatalogSnapshot
    notes: str = Field(default="")


def dump_project_document(document: ProjectDocument) -> str:
    """Return deterministic human-readable JSON for one validated document."""
    return json.dumps(document.model_dump(mode="json"), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def load_project_document(payload: str | bytes) -> ProjectDocument:
    """Parse and validate one project document from JSON text."""
    return ProjectDocument.model_validate_json(payload)


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
