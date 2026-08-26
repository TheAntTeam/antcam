"""Validated, immutable domain models for machining catalogs."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

_CATALOG_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


class _CatalogModel(BaseModel):
    """Common validation configuration for catalog records."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DataStatus(StrEnum):
    """Confidence classification for a catalog record's source data."""

    VERIFIED = "verified"
    CONSERVATIVE_PENDING_VERIFICATION = "conservative_pending_verification"


class ToolType(StrEnum):
    """Supported physical tool categories."""

    END_MILL = "end_mill"
    BALL = "ball"
    BULL = "bull"
    V_BIT = "v_bit"
    DRILL = "drill"
    TAP = "tap"
    BORE = "bore"
    THREAD_MILL = "thread_mill"
    T_SLOT = "t_slot"


class CoolingKind(StrEnum):
    """Cooling delivery modes supported by a machine."""

    NONE = "none"
    AIR = "air"
    MIST = "mist"
    FLOOD = "flood"


class _IdentifiedCatalogModel(_CatalogModel):
    """A catalog record addressed by a stable snake_case key."""

    id: str
    name: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    data_status: DataStatus = DataStatus.CONSERVATIVE_PENDING_VERIFICATION

    @model_validator(mode="after")
    def _validate_catalog_id(self) -> _IdentifiedCatalogModel:
        if _CATALOG_ID_PATTERN.fullmatch(self.id) is None:
            raise ValueError("id must be a lowercase snake_case catalog key")
        return self


class MachineProfile(_IdentifiedCatalogModel):
    """Machine capability and collision-envelope information."""

    vendor: str = Field(min_length=1)
    model: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    work_area_x_mm: float = Field(gt=0)
    work_area_y_mm: float = Field(gt=0)
    work_area_z_mm: float = Field(gt=0)
    spindle_power_w: float = Field(ge=0)
    min_rpm: float = Field(ge=0)
    max_rpm: float = Field(gt=0)
    max_feed_mm_min: float = Field(gt=0)
    collet_sizes_mm: tuple[float, ...] = Field(min_length=1)
    default_collet_size_mm: float = Field(gt=0)
    supported_cooling_ids: tuple[str, ...] = Field(min_length=1)
    default_cooling_id: str
    native_post: str = Field(min_length=1)
    spindle_diameter_mm: float = Field(gt=0)
    spindle_length_mm: float = Field(gt=0)
    rigid_tapping: bool = False
    three_d_toolpath: bool = False

    @model_validator(mode="after")
    def _validate_machine_consistency(self) -> MachineProfile:
        if self.max_rpm <= self.min_rpm:
            raise ValueError("max_rpm must be greater than min_rpm")
        if any(size <= 0 for size in self.collet_sizes_mm):
            raise ValueError("collet_sizes_mm must contain only positive values")
        if self.default_collet_size_mm not in self.collet_sizes_mm:
            raise ValueError("default_collet_size_mm must be included in collet_sizes_mm")
        if len(set(self.collet_sizes_mm)) != len(self.collet_sizes_mm):
            raise ValueError("collet_sizes_mm must not contain duplicates")
        if len(set(self.supported_cooling_ids)) != len(self.supported_cooling_ids):
            raise ValueError("supported_cooling_ids must not contain duplicates")
        if self.default_cooling_id not in self.supported_cooling_ids:
            raise ValueError("default_cooling_id must be included in supported_cooling_ids")
        return self


class Tool(_IdentifiedCatalogModel):
    """Physical cutting tool available to a machine."""

    tool_type: ToolType
    cutting_diameter_mm: float = Field(gt=0)
    shank_diameter_mm: float = Field(gt=0)
    flute_count: int = Field(gt=0)
    flute_length_mm: float = Field(gt=0)
    overall_length_mm: float = Field(gt=0)
    tool_material: str = Field(min_length=1)
    coating: str | None = None
    max_cutting_depth_mm: float | None = Field(default=None, gt=0)
    compatible_collet_sizes_mm: tuple[float, ...] = ()
    # Optional shape geometry used by the Phase 6 simulator; None falls back to
    # a conservative cylinder with an explicit warning (never breaks old JSON).
    v_bit_angle_deg: float | None = Field(default=None, gt=0, lt=180)
    tip_diameter_mm: float | None = Field(default=None, gt=0)
    t_slot_neck_diameter_mm: float | None = Field(default=None, gt=0)
    t_slot_head_diameter_mm: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_tool_consistency(self) -> Tool:
        if self.shank_diameter_mm < self.cutting_diameter_mm:
            raise ValueError("shank_diameter_mm must be at least cutting_diameter_mm")
        if self.flute_length_mm > self.overall_length_mm:
            raise ValueError("flute_length_mm must not exceed overall_length_mm")
        if any(size <= 0 for size in self.compatible_collet_sizes_mm):
            raise ValueError("compatible_collet_sizes_mm must contain only positive values")
        if len(set(self.compatible_collet_sizes_mm)) != len(self.compatible_collet_sizes_mm):
            raise ValueError("compatible_collet_sizes_mm must not contain duplicates")
        if self.t_slot_head_diameter_mm is not None and self.t_slot_neck_diameter_mm is not None:
            if self.t_slot_head_diameter_mm < self.t_slot_neck_diameter_mm:
                raise ValueError("t_slot_head_diameter_mm must be at least t_slot_neck_diameter_mm")
        return self


class MaterialProfile(_IdentifiedCatalogModel):
    """Machining parameters for a workpiece material."""

    family: str = Field(min_length=1)
    hardness: str | None = None
    surface_speed_m_min: float = Field(gt=0)
    chip_load_mm_tooth: float = Field(gt=0)
    plunge_ratio: float = Field(gt=0, le=1)
    machinability_factor: float = Field(gt=0, le=3)


class CoolingProfile(_IdentifiedCatalogModel):
    """Cooling effect applied to material cutting recommendations."""

    kind: CoolingKind
    surface_speed_factor: float = Field(gt=0, le=3)
    chip_load_factor: float = Field(gt=0, le=3)


class CatalogEnvelope[CatalogItem: _IdentifiedCatalogModel](_CatalogModel):
    """Versioned collection of catalog records of one kind."""

    schema_version: str = Field(min_length=1)
    catalog_id: str = Field(min_length=1)
    items: tuple[CatalogItem, ...]

    @model_validator(mode="after")
    def _validate_envelope(self) -> CatalogEnvelope[CatalogItem]:
        if _CATALOG_ID_PATTERN.fullmatch(self.catalog_id) is None:
            raise ValueError("catalog_id must be a lowercase snake_case catalog key")
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("catalog envelope contains duplicate item ids")
        return self
