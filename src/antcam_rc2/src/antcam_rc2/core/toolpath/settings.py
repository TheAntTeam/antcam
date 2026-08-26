"""Immutable planning settings shared by all Phase 4 strategies."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PlanningSettings(BaseModel):
    """Explicit safety and determinism inputs for one planning invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    clearance_z_mm: float = Field(gt=0)
    max_local_order_paths: int = Field(default=200, ge=1)
