"""Input and output contracts for feed and speed calculation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from antcam_rc2.core.databases.models import CoolingProfile, MachineProfile, MaterialProfile, Tool


class OperationFamily(StrEnum):
    """Calculation family, intentionally independent of project operations."""

    MILLING = "milling"
    DRILLING = "drilling"
    CARVING = "carving"


class ValueOrigin(StrEnum):
    """How a resolved calculation value was obtained."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"
    MANUAL_CLAMPED = "manual_clamped"


class FeedSpeedOverrides(BaseModel):
    """Optional manually requested values, still subject to hard safety limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rpm: float | None = Field(default=None, gt=0)
    cut_feed_mm_min: float | None = Field(default=None, gt=0)
    plunge_feed_mm_min: float | None = Field(default=None, gt=0)
    stepdown_mm: float | None = Field(default=None, gt=0)
    stepover_mm: float | None = Field(default=None, gt=0)


class FeedSpeedRequest(BaseModel):
    """Explicit inputs used by the deterministic calculator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: Tool
    material: MaterialProfile
    machine: MachineProfile
    cooling: CoolingProfile
    operation_family: OperationFamily
    effective_cutting_diameter_mm: float | None = Field(default=None, gt=0)
    radial_engagement_mm: float | None = Field(default=None, gt=0)
    axial_engagement_mm: float | None = Field(default=None, gt=0)
    pitch_mm: float | None = Field(default=None, gt=0)
    overrides: FeedSpeedOverrides = Field(default_factory=FeedSpeedOverrides)

    @model_validator(mode="after")
    def _validate_machine_cooling_compatibility(self) -> FeedSpeedRequest:
        if self.cooling.id not in self.machine.supported_cooling_ids:
            raise ValueError(f"cooling {self.cooling.id!r} is not supported by machine {self.machine.id!r}")
        return self


class FeedSpeedResult(BaseModel):
    """Resolved values and structured evidence for how they were produced."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_rpm: float
    rpm: float
    cut_feed_mm_min: float
    plunge_feed_mm_min: float
    stepdown_mm: float
    stepover_mm: float | None
    effective_surface_speed_m_min: float
    effective_chip_load_mm_tooth: float
    engagement_factor: float
    origins: dict[str, ValueOrigin]
    clamps_applied: tuple[str, ...]
