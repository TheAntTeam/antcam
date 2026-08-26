"""Pure, deterministic feed and speed calculation with hard safety clamps."""

from __future__ import annotations

import math
from dataclasses import dataclass

from antcam_rc2.core.databases.models import ToolType
from antcam_rc2.core.errors import OperationError
from antcam_rc2.core.feeds_speeds.models import (
    FeedSpeedRequest,
    FeedSpeedResult,
    OperationFamily,
    ValueOrigin,
)


@dataclass(frozen=True, slots=True)
class _FamilyPolicy:
    chip_load_factor: float
    stepdown_ratio: float
    stepover_ratio: float | None


_FAMILY_POLICIES: dict[OperationFamily, _FamilyPolicy] = {
    OperationFamily.MILLING: _FamilyPolicy(chip_load_factor=1.0, stepdown_ratio=0.5, stepover_ratio=0.4),
    OperationFamily.DRILLING: _FamilyPolicy(chip_load_factor=0.75, stepdown_ratio=1.0, stepover_ratio=None),
    OperationFamily.CARVING: _FamilyPolicy(chip_load_factor=0.5, stepdown_ratio=0.2, stepover_ratio=0.1),
}


class FeedsSpeedsCalculator:
    """Resolve automatic and manual machining parameters without side effects."""

    def calculate(self, request: FeedSpeedRequest) -> FeedSpeedResult:
        """Calculate values from an explicit request and record their provenance."""
        if request.tool.tool_type is ToolType.TAP:
            return self._calculate_tap(request)

        policy = _FAMILY_POLICIES[request.operation_family]
        diameter = request.effective_cutting_diameter_mm or request.tool.cutting_diameter_mm
        engagement_factor = self._engagement_factor(request, diameter)
        effective_surface_speed = (
            request.material.surface_speed_m_min
            * request.material.machinability_factor
            * request.cooling.surface_speed_factor
        )
        effective_chip_load = (
            request.material.chip_load_mm_tooth
            * request.cooling.chip_load_factor
            * policy.chip_load_factor
            * engagement_factor
        )
        requested_rpm = (1000.0 * effective_surface_speed) / (math.pi * diameter)
        clamps: list[str] = []
        origins: dict[str, ValueOrigin] = {}

        rpm, origins["rpm"] = self._resolve(
            field="rpm",
            automatic_value=requested_rpm,
            manual_value=request.overrides.rpm,
            lower=max(request.machine.min_rpm, 1.0),
            upper=request.machine.max_rpm,
            clamps=clamps,
        )
        automatic_cut_feed = rpm * effective_chip_load * request.tool.flute_count
        cut_feed, origins["cut_feed_mm_min"] = self._resolve(
            field="cut_feed_mm_min",
            automatic_value=automatic_cut_feed,
            manual_value=request.overrides.cut_feed_mm_min,
            lower=0.0,
            upper=request.machine.max_feed_mm_min,
            clamps=clamps,
        )
        automatic_plunge_feed = cut_feed * request.material.plunge_ratio
        plunge_feed, origins["plunge_feed_mm_min"] = self._resolve(
            field="plunge_feed_mm_min",
            automatic_value=automatic_plunge_feed,
            manual_value=request.overrides.plunge_feed_mm_min,
            lower=0.0,
            upper=request.machine.max_feed_mm_min,
            clamps=clamps,
        )
        automatic_stepdown = diameter * policy.stepdown_ratio
        stepdown, origins["stepdown_mm"] = self._resolve(
            field="stepdown_mm",
            automatic_value=automatic_stepdown,
            manual_value=request.overrides.stepdown_mm,
            lower=0.0,
            upper=automatic_stepdown,
            clamps=clamps,
        )

        stepover: float | None = None
        if policy.stepover_ratio is not None:
            automatic_stepover = diameter * policy.stepover_ratio
            stepover, origins["stepover_mm"] = self._resolve(
                field="stepover_mm",
                automatic_value=automatic_stepover,
                manual_value=request.overrides.stepover_mm,
                lower=0.0,
                upper=automatic_stepover,
                clamps=clamps,
            )

        return FeedSpeedResult(
            requested_rpm=requested_rpm,
            rpm=rpm,
            cut_feed_mm_min=cut_feed,
            plunge_feed_mm_min=plunge_feed,
            stepdown_mm=stepdown,
            stepover_mm=stepover,
            effective_surface_speed_m_min=effective_surface_speed,
            effective_chip_load_mm_tooth=effective_chip_load,
            engagement_factor=engagement_factor,
            origins=origins,
            clamps_applied=tuple(clamps),
        )

    @staticmethod
    def _calculate_tap(request: FeedSpeedRequest) -> FeedSpeedResult:
        """Resolve synchronized tapping feed: ``feed = rpm * pitch``.

        Rigid tapping couples spindle rotation to Z travel, so the cut and
        plunge feeds are the same synchronized value and stepover does not
        apply.  The machine must declare ``rigid_tapping`` support.
        """
        if not request.machine.rigid_tapping:
            raise OperationError(
                "tapping requires a machine with rigid tapping (spindle synchronization)",
                code="machine_spindle_sync_required",
            )
        if request.pitch_mm is None:
            raise OperationError("tapping requires pitch_mm")  # noqa: TRY003 - domain message
        diameter = request.effective_cutting_diameter_mm or request.tool.cutting_diameter_mm
        effective_surface_speed = (
            request.material.surface_speed_m_min
            * request.material.machinability_factor
            * request.cooling.surface_speed_factor
        )
        requested_rpm = (1000.0 * effective_surface_speed) / (math.pi * diameter)
        clamps: list[str] = []
        origins: dict[str, ValueOrigin] = {}
        rpm, origins["rpm"] = FeedsSpeedsCalculator._resolve(
            field="rpm",
            automatic_value=requested_rpm,
            manual_value=request.overrides.rpm,
            lower=max(request.machine.min_rpm, 1.0),
            upper=request.machine.max_rpm,
            clamps=clamps,
        )
        synchronized_feed = rpm * request.pitch_mm
        cut_feed, origins["cut_feed_mm_min"] = FeedsSpeedsCalculator._resolve(
            field="cut_feed_mm_min",
            automatic_value=synchronized_feed,
            manual_value=request.overrides.cut_feed_mm_min,
            lower=0.0,
            upper=request.machine.max_feed_mm_min,
            clamps=clamps,
        )
        plunge_feed, origins["plunge_feed_mm_min"] = FeedsSpeedsCalculator._resolve(
            field="plunge_feed_mm_min",
            automatic_value=synchronized_feed,
            manual_value=request.overrides.plunge_feed_mm_min,
            lower=0.0,
            upper=request.machine.max_feed_mm_min,
            clamps=clamps,
        )
        # Tapping is a single synchronized pass; stepdown mirrors the depth
        # policy (unused by the strategy, kept for contract completeness).
        stepdown, origins["stepdown_mm"] = FeedsSpeedsCalculator._resolve(
            field="stepdown_mm",
            automatic_value=diameter * _FAMILY_POLICIES[OperationFamily.DRILLING].stepdown_ratio,
            manual_value=request.overrides.stepdown_mm,
            lower=0.0,
            upper=diameter * _FAMILY_POLICIES[OperationFamily.DRILLING].stepdown_ratio,
            clamps=clamps,
        )
        return FeedSpeedResult(
            requested_rpm=requested_rpm,
            rpm=rpm,
            cut_feed_mm_min=cut_feed,
            plunge_feed_mm_min=plunge_feed,
            stepdown_mm=stepdown,
            stepover_mm=None,
            effective_surface_speed_m_min=effective_surface_speed,
            effective_chip_load_mm_tooth=0.0,
            engagement_factor=1.0,
            origins=origins,
            clamps_applied=tuple(clamps),
        )

    @staticmethod
    def _engagement_factor(request: FeedSpeedRequest, diameter: float) -> float:
        if request.radial_engagement_mm is None or request.axial_engagement_mm is None:
            return 1.0
        radial_ratio = min(request.radial_engagement_mm / diameter, 1.0)
        axial_ratio = min(request.axial_engagement_mm / diameter, 1.0)
        return math.sqrt(radial_ratio * axial_ratio)

    @staticmethod
    def _resolve(
        *,
        field: str,
        automatic_value: float,
        manual_value: float | None,
        lower: float,
        upper: float,
        clamps: list[str],
    ) -> tuple[float, ValueOrigin]:
        value = automatic_value if manual_value is None else manual_value
        origin = ValueOrigin.AUTOMATIC if manual_value is None else ValueOrigin.MANUAL
        if value < lower:
            clamps.append(f"{field}:min")
            return lower, ValueOrigin.MANUAL_CLAMPED if manual_value is not None else origin
        if value > upper:
            clamps.append(f"{field}:max")
            return upper, ValueOrigin.MANUAL_CLAMPED if manual_value is not None else origin
        return value, origin
