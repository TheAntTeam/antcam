"""Persistent project setup models and safe geometry-reference contracts."""

from antcam_rc2.core.project.fixture_library import FixtureLibrary
from antcam_rc2.core.project.geometry_refs import (
    bind_geometry,
    create_geometry_ref,
    entity_fingerprint,
    resolve_geometry_ref,
    scene_fingerprint,
)
from antcam_rc2.core.project.models import (
    CatalogSnapshot,
    Fixture,
    FixtureKind,
    GeometryBinding,
    GeometryRef,
    Operation,
    OperationParameters,
    OperationType,
    Project,
    SolidBinding,
    SolidRef,
    Stock,
    StockOrigin,
    WorkCoordinateSystem,
)
from antcam_rc2.core.project.persistence import (
    PROJECT_DOCUMENT_SCHEMA_VERSION,
    ProjectDocument,
    dump_project_document,
    load_project_document,
    read_project_document,
    write_project_document,
)
from antcam_rc2.core.project.repository import InMemoryProjectRepository, ProjectRepository
from antcam_rc2.core.project.solid_refs import (
    bind_solid,
    create_solid_ref,
    resolve_solid_ref,
    solid_scene_fingerprint,
)

__all__ = [
    "CatalogSnapshot",
    "Fixture",
    "FixtureKind",
    "FixtureLibrary",
    "GeometryBinding",
    "GeometryRef",
    "InMemoryProjectRepository",
    "Operation",
    "OperationParameters",
    "OperationType",
    "PROJECT_DOCUMENT_SCHEMA_VERSION",
    "Project",
    "ProjectDocument",
    "ProjectRepository",
    "SolidBinding",
    "SolidRef",
    "Stock",
    "StockOrigin",
    "WorkCoordinateSystem",
    "bind_geometry",
    "bind_solid",
    "create_geometry_ref",
    "create_solid_ref",
    "dump_project_document",
    "entity_fingerprint",
    "load_project_document",
    "read_project_document",
    "resolve_geometry_ref",
    "resolve_solid_ref",
    "scene_fingerprint",
    "solid_scene_fingerprint",
    "write_project_document",
]
