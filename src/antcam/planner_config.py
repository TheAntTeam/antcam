"""Shared runtime configuration for planner entry points.

This module keeps CLI, viewer, and VS Code launcher configuration aligned
while the planner evolves toward real tool and material libraries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from antcam.machining_library import get_material_profile, get_tool_library
from antcam.path_generator import (
    AutoToolpathPlanner,
    MachiningParameterResolver,
    ParameterOverrides,
    ToolManager,
)


@dataclass(frozen=True)
class PlannerRuntimeConfig:
    """User-facing planner settings shared by CLI and viewer entry points."""

    parameter_mode: str = "manual"
    safe_z_offset: float = 5.0
    clearance_z: float = 1.0
    cut_feed: float = 300.0
    plunge_feed: float = 120.0
    max_stepdown: float = 4.0
    tool_diameter: float = 6.0
    stepover_ratio: float = 0.6
    lead_in_distance: float = 0.0
    lead_out_distance: float = 0.0
    profile_stock_allowance: float = 0.0
    roughing_distance: float = 1.0
    enable_perimeter_profile_fallback: bool = False
    profile_rough_only: bool = False
    piece_roughing_only: bool = False
    tool_library: str = "standard_mm"
    tool_library_file: Optional[str] = None
    material_profile: str = "generic"
    material_profile_file: Optional[str] = None
    drill_tool_id: Optional[str] = None
    mill_tool_id: Optional[str] = None
    automatic_surface_speed_m_per_min: Optional[float] = None
    automatic_chip_load: Optional[float] = None
    automatic_plunge_ratio: Optional[float] = None
    automatic_stepdown_ratio: Optional[float] = None
    automatic_lead_ratio: Optional[float] = None
    material_factor: float = 1.0

    def __post_init__(self) -> None:
        mode = self.parameter_mode.strip().lower()
        if mode not in {"manual", "automatic"}:
            raise ValueError("parameter_mode must be 'manual' or 'automatic'")
        object.__setattr__(self, "parameter_mode", mode)
        object.__setattr__(self, "tool_library", self.tool_library.strip().lower())
        object.__setattr__(self, "material_profile", self.material_profile.strip().lower())
        object.__setattr__(self, "tool_library_file", self.tool_library_file.strip() if self.tool_library_file else None)
        object.__setattr__(self, "material_profile_file", self.material_profile_file.strip() if self.material_profile_file else None)

        tool_library = get_tool_library(self.tool_library, self.tool_library_file)
        get_material_profile(self.material_profile, self.material_profile_file)

        positive_fields = (
            ("safe_z_offset", self.safe_z_offset),
            ("clearance_z", self.clearance_z),
            ("cut_feed", self.cut_feed),
            ("plunge_feed", self.plunge_feed),
            ("max_stepdown", self.max_stepdown),
            ("tool_diameter", self.tool_diameter),
            ("stepover_ratio", self.stepover_ratio),
            ("material_factor", self.material_factor),
        )
        non_negative_fields = (
            ("lead_in_distance", self.lead_in_distance),
            ("lead_out_distance", self.lead_out_distance),
            ("profile_stock_allowance", self.profile_stock_allowance),
            ("roughing_distance", self.roughing_distance),
        )
        optional_positive_fields = (
            ("automatic_surface_speed_m_per_min", self.automatic_surface_speed_m_per_min),
            ("automatic_chip_load", self.automatic_chip_load),
            ("automatic_plunge_ratio", self.automatic_plunge_ratio),
            ("automatic_stepdown_ratio", self.automatic_stepdown_ratio),
            ("automatic_lead_ratio", self.automatic_lead_ratio),
        )

        for field_name, value in positive_fields:
            if float(value) <= 0.0:
                raise ValueError(f"{field_name} must be greater than 0")
        for field_name, value in non_negative_fields:
            if float(value) < 0.0:
                raise ValueError(f"{field_name} must be non-negative")
        for field_name, value in optional_positive_fields:
            if value is not None and float(value) <= 0.0:
                raise ValueError(f"{field_name} must be greater than 0 when provided")

        known_tool_ids = {tool.tool_id for tool in tool_library}
        if self.drill_tool_id and self.drill_tool_id not in known_tool_ids:
            raise ValueError(f"drill_tool_id '{self.drill_tool_id}' is not present in tool_library '{self.tool_library}'")
        if self.mill_tool_id and self.mill_tool_id not in known_tool_ids:
            raise ValueError(f"mill_tool_id '{self.mill_tool_id}' is not present in tool_library '{self.tool_library}'")

    def build_tool_manager(self) -> ToolManager:
        strategy_tool_map: dict[str, str] = {}
        if self.drill_tool_id:
            strategy_tool_map["drilling"] = self.drill_tool_id
        if self.mill_tool_id:
            for strategy in ("slot_milling", "cavity_clearing", "2p5d_profile"):
                strategy_tool_map[strategy] = self.mill_tool_id

        return ToolManager(
            tools=get_tool_library(self.tool_library, self.tool_library_file),
            strategy_tool_map=strategy_tool_map or None,
            preferred_diameter=self.tool_diameter,
            workpiece_material=self.material_profile,
        )

    def build_parameter_resolver(self) -> MachiningParameterResolver:
        material_profile = get_material_profile(self.material_profile, self.material_profile_file)
        if self.parameter_mode == "automatic":
            base_overrides = ParameterOverrides()
        else:
            base_overrides = ParameterOverrides(
                cut_feed=self.cut_feed,
                plunge_feed=self.plunge_feed,
                max_stepdown=self.max_stepdown,
                stepover_ratio=self.stepover_ratio,
                lead_in_distance=self.lead_in_distance,
                lead_out_distance=self.lead_out_distance,
            )

        return MachiningParameterResolver(
            mode=self.parameter_mode,
            base_overrides=base_overrides,
            automatic_mode_overrides=material_profile.automatic_mode_overrides,
            automatic_strategy_overrides=material_profile.automatic_strategy_overrides,
            automatic_strategy_mode_overrides=material_profile.automatic_strategy_mode_overrides,
            automatic_feature_overrides=material_profile.automatic_feature_overrides,
            automatic_surface_speed_m_per_min=self.automatic_surface_speed_m_per_min or material_profile.surface_speed_m_per_min,
            automatic_chip_load=self.automatic_chip_load or material_profile.chip_load,
            automatic_plunge_ratio=self.automatic_plunge_ratio or material_profile.plunge_ratio,
            automatic_stepdown_ratio=self.automatic_stepdown_ratio or material_profile.stepdown_ratio,
            automatic_lead_ratio=self.automatic_lead_ratio or material_profile.lead_ratio,
            material_factor=material_profile.material_factor * self.material_factor,
        )

    def build_planner(self, working_plane_normal: Iterable[float]) -> AutoToolpathPlanner:
        return AutoToolpathPlanner(
            working_plane_normal=tuple(float(v) for v in working_plane_normal),
            safe_z_offset=self.safe_z_offset,
            clearance_z=self.clearance_z,
            plunge_feed=self.plunge_feed,
            cut_feed=self.cut_feed,
            max_stepdown=self.max_stepdown,
            tool_diameter=self.tool_diameter,
            stepover_ratio=self.stepover_ratio,
            parameter_mode=self.parameter_mode,
            lead_in_distance=self.lead_in_distance,
            lead_out_distance=self.lead_out_distance,
            profile_stock_allowance=self.profile_stock_allowance,
            roughing_distance=self.roughing_distance,
            enable_perimeter_profile_fallback=self.enable_perimeter_profile_fallback,
            profile_rough_only=self.profile_rough_only,
            piece_roughing_only=self.piece_roughing_only,
            tool_manager=self.build_tool_manager(),
            parameter_resolver=self.build_parameter_resolver(),
        )