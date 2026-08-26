"""Hierarchy of domain exceptions for AntCAM RC2."""

from __future__ import annotations


class AntcamError(Exception):
    """Base class for all AntCAM RC2 errors.

    Attributes:
        code: stable, machine-readable error identifier.
    """

    code: str = "antcam_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class ConfigurationError(AntcamError):
    """Invalid or inconsistent application configuration."""

    code = "configuration_error"


class CatalogError(ConfigurationError):
    """A catalog lookup or catalog bundle relationship is invalid."""

    code = "catalog_error"


class UnsupportedFormatError(AntcamError):
    """A file format or operation is not supported."""

    code = "unsupported_format"


class GeometryError(AntcamError):
    """Invalid or degenerate geometric input."""

    code = "geometry_error"


class ProjectError(AntcamError):
    """A project setup, catalog binding, or persistence action is invalid."""

    code = "project_error"


class GeometryReferenceError(ProjectError):
    """A persisted project reference no longer identifies safe geometry."""

    code = "geometry_reference_error"


class OperationError(AntcamError):
    """An operation could not be created or executed."""

    code = "operation_error"


class ToolpathError(AntcamError):
    """Toolpath generation failed."""

    code = "toolpath_error"


class SimulationError(AntcamError):
    """Simulation or collision-checking failed."""

    code = "simulation_error"


class PostProcessorError(AntcamError):
    """Post-processing to G-code failed."""

    code = "post_processor_error"
