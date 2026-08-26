"""Immutable, JSON-serializable project setup and operation intent models."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from antcam_rc2.core.feeds_speeds.models import FeedSpeedOverrides
from antcam_rc2.core.geometry3d.scene import SolidSourceInfo
from antcam_rc2.core.io.scene import SourceInfo
from antcam_rc2.core.units import UnitSystem

_CATALOG_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_RUNTIME_ID_PATTERN = re.compile(r"^(?:proj|fix|op)_[0-9a-f]{8}$")
_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CATALOG_VERSION_KEYS = frozenset({"machines", "tools", "materials", "cooling"})


class _ProjectModel(BaseModel):
    """Shared strict, immutable configuration for persistent project data."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class StockOrigin(StrEnum):
    """Stock placement relative to the active work coordinate system."""

    CENTER_XY_TOP_Z = "center_xy_top_z"
    CORNER_XY_TOP_Z = "corner_xy_top_z"
    CENTER_XY_ZERO_Z = "center_xy_zero_z"
    CORNER_XY_ZERO_Z = "corner_xy_zero_z"


class FixtureKind(StrEnum):
    """Fixture role used by later collision and setup validation phases."""

    FIXED = "fixed"
    VISE = "vise"
    SCREW = "screw"


class OperationType(StrEnum):
    """Stable vocabulary for the planned operation plugins.

    This enum intentionally provides names only. Strategy validation and the
    operation registry are introduced by Phase 4.
    """

    FACE_TOP = "face_top"
    ROUGHING = "roughing"
    FACING = "facing"
    POCKETING = "pocketing"
    PROFILING = "profiling"
    V_CARVE_ROUGHING = "v_carve_roughing"
    V_CARVING = "v_carving"
    ENGRAVING = "engraving"
    SLOTTING = "slotting"
    CHAMFERING = "chamfering"
    FILLETTING = "filleting"
    HOLE_POCKETING = "hole_pocketing"
    THREAD_MILLING = "thread_milling"
    T_SLOTTING = "t_slotting"
    HOLES = "holes"
    DRILL = "drill"
    TAPPING = "tapping"
    BORING = "boring"


class WorkCoordinateSystem(_ProjectModel):
    """Active work coordinate system and its offset in millimetres."""

    name: str = Field(default="G54", pattern=r"^G5[4-9]$")
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    offset_z_mm: float = 0.0


class Stock(_ProjectModel):
    """The raw workpiece material positioned in the project WCS."""

    width_mm: float = Field(gt=0)
    length_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    position_x_mm: float = 0.0
    position_y_mm: float = 0.0
    position_z_mm: float = 0.0
    material_id: str = Field(min_length=1)
    origin: StockOrigin = StockOrigin.CENTER_XY_TOP_Z

    @model_validator(mode="after")
    def _validate_material_key(self) -> Stock:
        _validate_catalog_key(self.material_id, "material_id")
        return self


class Fixture(_ProjectModel):
    """A box-shaped workholding element; mesh loading for realistic rendering.

    If ``mesh_path`` is set and points to a valid STEP/STL file (relative to the
    fixture library meshes directory or absolute), the fixture is rendered as a
    mesh instead of a box. The mesh is translated to the fixture's position.
    Collision detection (Phase 6+) will use the mesh for precise checks when
    available, falling back to the bounding box otherwise.
    """

    id: str
    name: str = Field(min_length=1)
    kind: FixtureKind = FixtureKind.FIXED
    width_mm: float = Field(gt=0)
    length_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    position_x_mm: float = 0.0
    position_y_mm: float = 0.0
    position_z_mm: float = 0.0
    mesh_path: str | None = Field(
        default=None,
        description=(
            "Optional path to a STEP/STL mesh file for realistic rendering and "
            "precise collision detection. Stored as a relative path to the "
            "fixture library meshes directory (e.g., 'meshes/fix_abc12345.step')."
        ),
    )
    # Screw-specific fields (used when kind == SCREW)
    screw_diameter_mm: float | None = Field(default=None, gt=0)
    screw_length_mm: float | None = Field(default=None, gt=0)
    hole_diameter_mm: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_fixture_id(self) -> Fixture:
        _validate_runtime_id(self.id, "fix")
        return self


class GeometryBinding(_ProjectModel):
    """Persistent provenance for a transient imported geometry scene."""

    source: SourceInfo
    scene_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class GeometryRef(_ProjectModel):
    """A safe reference to one entity in a transient GeometryScene."""

    layer_name: str = Field(min_length=1)
    entity_index: int = Field(ge=0)
    entity_type: str = Field(min_length=1)
    entity_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SolidBinding(_ProjectModel):
    """Persistent provenance for a transient imported 3D solid scene."""

    source: SolidSourceInfo
    scene_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SolidRef(_ProjectModel):
    """A safe reference to one feature in a transient SolidScene."""

    body_index: int = Field(ge=0)
    feature_index: int = Field(ge=0)
    feature_type: str = Field(min_length=1)
    feature_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class OperationParameters(_ProjectModel):
    """Durable strategy-neutral operation intent.

    Defaults may be resolved by the Phase 4 registry. Calculated feeds, pass
    counts, and toolpaths do not belong in this persistent model.
    """

    tolerance_mm: float = Field(default=0.1, gt=0)
    depth_mm: float | None = Field(default=None, gt=0)
    stepdown_mm: float | None = Field(default=None, gt=0)
    stepover_mm: float | None = Field(default=None, gt=0)
    finishing_passes: int = Field(default=0, ge=0)
    stock_allowance_mm: float = Field(default=0.0, ge=0)
    climb_cut: bool = False
    optimize_path_order: bool = True
    feed_speed_overrides: FeedSpeedOverrides = Field(default_factory=FeedSpeedOverrides)
    strategy_parameters: dict[str, JsonValue] = Field(default_factory=dict)


class Operation(_ProjectModel):
    """One ordered, durable machining-operation definition."""

    id: str
    name: str = Field(min_length=1)
    operation_type: OperationType
    enabled: bool = True
    tool_id: str = Field(min_length=1)
    cooling_id: str = Field(min_length=1)
    geometry_refs: tuple[GeometryRef, ...] = ()
    solid_refs: tuple[SolidRef, ...] = ()
    parameters: OperationParameters = Field(default_factory=OperationParameters)

    @model_validator(mode="after")
    def _validate_operation_values(self) -> Operation:
        _validate_runtime_id(self.id, "op")
        _validate_catalog_key(self.tool_id, "tool_id")
        _validate_catalog_key(self.cooling_id, "cooling_id")
        return self


class CatalogSnapshot(_ProjectModel):
    """Catalog schema versions observed when the project was saved."""

    versions: dict[str, str]

    @model_validator(mode="after")
    def _validate_versions(self) -> CatalogSnapshot:
        if set(self.versions) != _CATALOG_VERSION_KEYS or any(not value for value in self.versions.values()):
            raise ValueError("catalog versions must include machines, tools, materials, and cooling")
        return self


class Project(_ProjectModel):
    """Complete persistent project setup, excluding transient imported geometry."""

    id: str
    name: str = Field(min_length=1)
    created_at: datetime
    modified_at: datetime
    units: UnitSystem = UnitSystem.METRIC
    wcs: WorkCoordinateSystem = Field(default_factory=WorkCoordinateSystem)
    machine_id: str = Field(min_length=1)
    stock: Stock
    fixtures: tuple[Fixture, ...] = ()
    operations: tuple[Operation, ...] = ()
    geometry_binding: GeometryBinding | None = None

    @model_validator(mode="after")
    def _validate_project(self) -> Project:
        _validate_runtime_id(self.id, "proj")
        _validate_catalog_key(self.machine_id, "machine_id")
        if self.modified_at < self.created_at:
            raise ValueError("modified_at must not precede created_at")
        _validate_unique_ids(self.fixtures, "fixture")
        _validate_unique_ids(self.operations, "operation")
        return self


def _validate_runtime_id(value: str, prefix: str) -> None:
    if _RUNTIME_ID_PATTERN.fullmatch(value) is None or not value.startswith(f"{prefix}_"):
        raise ValueError(f"id must be a valid {prefix}_xxxxxxxx runtime id")


def _validate_catalog_key(value: str, field_name: str) -> None:
    if _CATALOG_KEY_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase snake_case catalog key")


def _validate_unique_ids(records: tuple[Fixture, ...] | tuple[Operation, ...], label: str) -> None:
    ids = [record.id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError(f"project contains duplicate {label} ids")
