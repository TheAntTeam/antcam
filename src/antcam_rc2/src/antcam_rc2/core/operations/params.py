"""Strict per-strategy parameter schemas for the 18 registered operations.

Each model mirrors the JSON-only ``strategy_parameters`` dict persisted on an
``Operation`` and is validated by the registry before planning.  Common
parameters (depth, stepdown, stepover, tolerance, ...) live in
``OperationParameters`` and are not duplicated here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from antcam_rc2.core.operations.contracts import StrategyParameters


class ProfileParameters(StrategyParameters):
    """Profiling: which side of the selected loops the tool centre follows."""

    side: Literal["outside", "inside"] = "outside"
    entry_mode: Literal["plunge", "ramp"] = "plunge"


class FaceTopParameters(StrategyParameters):
    """Face Top levels the stock top; depth/allowance are common parameters."""


class FacingParameters(StrategyParameters):
    """Facing rasterizes a selected region at target depth."""


class RoughingParameters(StrategyParameters):
    """Region-based roughing with stock allowance; no per-strategy inputs."""


class PocketParameters(StrategyParameters):
    """Contour-parallel pocket clearing; no per-strategy inputs."""


class SlottingParameters(StrategyParameters):
    """Slotting style: centerline pass or multi-lane raster along the axis."""

    style: Literal["centerline", "multi_lane"] = "centerline"


class TSlotParameters(StrategyParameters):
    """T-slot cutting; the T-slot tool is validated by the registry."""


class DrillParameters(StrategyParameters):
    """Straight drilling cycle; no per-strategy inputs."""


class BoringParameters(StrategyParameters):
    """Boring: plunge, dwell at the bottom, retract."""

    dwell_seconds: float = Field(default=0.5, gt=0, le=30.0)


class HolesParameters(StrategyParameters):
    """Holes orchestrates drill or bore cycles from one selection."""

    hole_type: Literal["drill", "bore"] = "drill"
    dwell_seconds: float = Field(default=0.5, gt=0, le=30.0)


class HolePocketingParameters(StrategyParameters):
    """Hole pocketing: concentric circular clearing to ``hole_diameter_mm``."""

    hole_diameter_mm: float = Field(gt=0)


class ThreadMillingParameters(StrategyParameters):
    """Thread milling: helical interpolation with pitch and thread diameter."""

    pitch_mm: float = Field(gt=0)
    thread_diameter_mm: float = Field(gt=0)
    direction: Literal["up", "down"] = "up"


class TappingParameters(StrategyParameters):
    """Rigid tapping: synchronized plunge/retract at ``feed = rpm * pitch``."""

    pitch_mm: float = Field(gt=0)


class VCarveRoughingParameters(StrategyParameters):
    """V-carve roughing: clears a V-groove with an end mill, leaving allowance."""

    angle_deg: float = Field(gt=0, lt=180)
    width_mm: float = Field(gt=0)


class VCarvingParameters(StrategyParameters):
    """V-carving: follows centerlines with a V-bit at width-derived depth."""

    angle_deg: float = Field(gt=0, lt=180)
    width_mm: float = Field(gt=0)


class EngravingParameters(StrategyParameters):
    """Engraving: centerline following at shallow depth; no per-strategy inputs."""


class ChamferingParameters(StrategyParameters):
    """Chamfering: edge tracing at a depth derived from chamfer width/angle."""

    angle_deg: float = Field(default=45.0, gt=0, lt=180)
    width_mm: float = Field(gt=0)


class FilletingParameters(StrategyParameters):
    """Filleting: 2.5D requires explicit profile parameters; 3D is gated."""

    radius_mm: float = Field(gt=0)
