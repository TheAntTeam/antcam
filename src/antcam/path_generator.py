"""Automatic 2.5D toolpath planning primitives.

This module introduces a first CAM planning layer that converts recognized
features and optional contour perimeters into neutral motion commands.

The generated plan is intentionally post-processor agnostic and can be
serialized to JSON or converted later to machine-specific G-code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional

import numpy as np

Point3 = tuple[float, float, float]


@dataclass
class PlanarRegion:
    """Lightweight planar machining region used by cavity clearing."""

    center: np.ndarray
    basis_u: np.ndarray
    basis_v: np.ndarray
    span_u: float
    span_v: float
    projection: float
    boundary_loops: list[list[np.ndarray]] = field(default_factory=list)


@dataclass
class MotionCommand:
    """One neutral motion command in machine coordinates."""

    move: str
    point: Point3
    feed: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "move": self.move,
            "x": round(float(self.point[0]), 4),
            "y": round(float(self.point[1]), 4),
            "z": round(float(self.point[2]), 4),
        }
        if self.feed is not None:
            data["feed"] = round(float(self.feed), 4)
        return data


@dataclass
class ToolpathOperation:
    """A machining operation made of a linear sequence of motion commands."""

    op_id: str
    strategy: str
    feature_type: str
    motions: List[MotionCommand] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "strategy": self.strategy,
            "feature_type": self.feature_type,
            "metadata": self.metadata,
            "motions": [motion.to_dict() for motion in self.motions],
        }


@dataclass
class ToolpathPlan:
    """Serializable neutral plan output for the first CAM stage."""

    working_plane_normal: Point3
    safe_projection: float
    operations: List[ToolpathOperation]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "working_plane_normal": [round(float(v), 6) for v in self.working_plane_normal],
            "safe_projection": round(float(self.safe_projection), 4),
            "warnings": list(self.warnings),
            "operation_count": len(self.operations),
            "operations": [operation.to_dict() for operation in self.operations],
        }


@dataclass
class ToolDefinition:
    """Serializable tool descriptor used by planning and future tool libraries."""

    tool_id: str
    name: str
    tool_type: str
    diameter: float
    flute_count: int = 2
    spindle_speed: Optional[float] = None
    cut_feed: Optional[float] = None
    plunge_feed: Optional[float] = None
    max_stepdown: Optional[float] = None
    stepover_ratio: Optional[float] = None
    flute_length: Optional[float] = None
    stickout: Optional[float] = None
    tool_material: Optional[str] = None
    compatible_materials: Optional[tuple[str, ...]] = None
    max_depth: Optional[float] = None
    supported_strategies: Optional[tuple[str, ...]] = None
    supported_modes: Optional[tuple[str, ...]] = None


@dataclass
class OperationRequest:
    """Compact planning request used by tool and parameter resolution."""

    strategy: str
    feature_type: str
    operation_mode: Optional[str] = None
    depth: float = 0.0
    through: bool = False
    diameter: Optional[float] = None
    width: Optional[float] = None
    length: Optional[float] = None
    span_u: Optional[float] = None
    span_v: Optional[float] = None


@dataclass
class ParameterOverrides:
    """Optional per-context machining parameter overrides."""

    cut_feed: Optional[float] = None
    plunge_feed: Optional[float] = None
    spindle_speed: Optional[float] = None
    max_stepdown: Optional[float] = None
    stepover_ratio: Optional[float] = None
    lead_in_distance: Optional[float] = None
    lead_out_distance: Optional[float] = None


@dataclass
class AutomaticParameterOverrides:
    """Optional automatic-tuning inputs that vary by strategy or feature."""

    surface_speed_m_per_min: Optional[float] = None
    chip_load: Optional[float] = None
    plunge_ratio: Optional[float] = None
    stepdown_ratio: Optional[float] = None
    lead_ratio: Optional[float] = None
    stepover_ratio: Optional[float] = None
    material_factor: Optional[float] = None


@dataclass
class MachiningParameters:
    """Resolved machining parameters for one concrete planning request."""

    tool: ToolDefinition
    parameter_mode: str
    cut_feed: float
    plunge_feed: float
    spindle_speed: Optional[float]
    max_stepdown: float
    stepover_ratio: float
    lead_in_distance: float
    lead_out_distance: float


@dataclass(frozen=True)
class MachiningFeatureContext:
    """Feature-based CAM classification attached to emitted operations."""

    machining_class: str
    feature_subtype: str
    geometry_source: str


class ToolManager:
    """Resolve the tool used for one operation from a local tool set or future library."""

    def __init__(
        self,
        tools: Optional[Iterable[ToolDefinition]] = None,
        strategy_tool_map: Optional[dict[str, str]] = None,
        feature_tool_map: Optional[dict[str, str]] = None,
        preferred_diameter: Optional[float] = None,
        workpiece_material: Optional[str] = None,
    ) -> None:
        tool_list = list(tools or [])
        self._tools = {tool.tool_id: tool for tool in tool_list}
        self.strategy_tool_map = dict(strategy_tool_map or {})
        self.feature_tool_map = dict(feature_tool_map or {})
        self.preferred_diameter = float(preferred_diameter) if preferred_diameter is not None else None
        self.workpiece_material = workpiece_material.strip().lower() if workpiece_material else None

    @staticmethod
    def build_default_tools(preferred_diameter: float) -> list[ToolDefinition]:
        diameter = max(0.1, float(preferred_diameter))
        small = max(diameter * 0.5, 1.0)
        medium = diameter
        large = max(diameter * 1.5, diameter + 1.0)
        return [
            ToolDefinition(
                tool_id="drill_small",
                name=f"{small:g} mm drill",
                tool_type="drill",
                diameter=small,
                flute_count=2,
            ),
            ToolDefinition(
                tool_id="drill_medium",
                name=f"{medium:g} mm drill",
                tool_type="drill",
                diameter=medium,
                flute_count=2,
            ),
            ToolDefinition(
                tool_id="drill_large",
                name=f"{large:g} mm drill",
                tool_type="drill",
                diameter=large,
                flute_count=2,
            ),
            ToolDefinition(
                tool_id="endmill_small",
                name=f"{small:g} mm end mill",
                tool_type="end_mill",
                diameter=small,
                flute_count=2,
            ),
            ToolDefinition(
                tool_id="endmill_medium",
                name=f"{medium:g} mm end mill",
                tool_type="end_mill",
                diameter=medium,
                flute_count=3,
            ),
            ToolDefinition(
                tool_id="endmill_large",
                name=f"{large:g} mm end mill",
                tool_type="end_mill",
                diameter=large,
                flute_count=2,
            ),
        ]

    def get_tool(self, tool_id: str) -> Optional[ToolDefinition]:
        return self._tools.get(tool_id)

    def select_tool(self, request: OperationRequest) -> Optional[ToolDefinition]:
        explicit_tool_id = self.feature_tool_map.get(request.feature_type) or self.strategy_tool_map.get(request.strategy)
        if explicit_tool_id is not None:
            tool = self.get_tool(explicit_tool_id)
            if tool is None or not self._tool_supports_request(tool, request):
                return None
            return tool

        required_type = "drill" if request.strategy == "drilling" else "end_mill"
        candidates = [
            tool
            for tool in self._tools.values()
            if tool.tool_type == required_type and self._tool_supports_request(tool, request)
        ]
        if not candidates:
            return None

        size_limit = self._get_request_size_limit(request)
        if size_limit is None:
            return self._select_preferred_tool(candidates)

        fitting = [tool for tool in candidates if tool.diameter <= size_limit + 1e-9]
        if fitting:
            return self._select_preferred_tool(fitting)
        return self._select_preferred_tool(candidates)

    def _select_preferred_tool(self, candidates: list[ToolDefinition]) -> ToolDefinition:
        if self.preferred_diameter is None:
            return min(
                candidates,
                key=lambda tool: (
                    -tool.diameter,
                    len(tool.supported_modes) if tool.supported_modes is not None else 99,
                    len(tool.supported_strategies) if tool.supported_strategies is not None else 99,
                ),
            )
        return min(
            candidates,
            key=lambda tool: (
                abs(tool.diameter - self.preferred_diameter),
                len(tool.supported_modes) if tool.supported_modes is not None else 99,
                len(tool.supported_strategies) if tool.supported_strategies is not None else 99,
                -tool.diameter,
            ),
        )

    @staticmethod
    def _get_request_size_limit(request: OperationRequest) -> Optional[float]:
        if request.strategy == "drilling" and request.diameter is not None:
            return float(request.diameter)
        if request.strategy == "slot_milling" and request.width is not None:
            return float(request.width)
        if request.span_u is not None and request.span_v is not None:
            return float(min(request.span_u, request.span_v))
        return None

    def _tool_supports_request(self, tool: ToolDefinition, request: OperationRequest) -> bool:
        if tool.supported_strategies is not None and request.strategy not in tool.supported_strategies:
            return False
        if request.operation_mode is not None and tool.supported_modes is not None and request.operation_mode not in tool.supported_modes:
            return False
        if self.workpiece_material is not None and tool.compatible_materials is not None:
            compatible_materials = {material.strip().lower() for material in tool.compatible_materials}
            if self.workpiece_material not in compatible_materials:
                return False
        if tool.flute_length is not None and float(request.depth) > float(tool.flute_length) + 1e-9:
            return False
        if tool.stickout is not None and float(request.depth) > float(tool.stickout) + 1e-9:
            return False
        if tool.max_depth is not None and float(request.depth) > float(tool.max_depth) + 1e-9:
            return False
        return True


class MachiningParameterResolver:
    """Resolve feeds, stepdown, stepover, and lead distances in manual or automatic mode."""

    def __init__(
        self,
        mode: str = "manual",
        base_overrides: Optional[ParameterOverrides] = None,
        strategy_overrides: Optional[dict[str, ParameterOverrides]] = None,
        feature_overrides: Optional[dict[str, ParameterOverrides]] = None,
        automatic_mode_overrides: Optional[dict[str, AutomaticParameterOverrides]] = None,
        automatic_strategy_overrides: Optional[dict[str, AutomaticParameterOverrides]] = None,
        automatic_strategy_mode_overrides: Optional[dict[str, dict[str, AutomaticParameterOverrides]]] = None,
        automatic_feature_overrides: Optional[dict[str, AutomaticParameterOverrides]] = None,
        automatic_surface_speed_m_per_min: float = 120.0,
        automatic_chip_load: float = 0.02,
        automatic_plunge_ratio: float = 0.35,
        automatic_stepdown_ratio: float = 0.5,
        automatic_lead_ratio: float = 0.25,
        material_factor: float = 1.0,
    ) -> None:
        self.mode = mode
        self.base_overrides = base_overrides or ParameterOverrides()
        self.strategy_overrides = dict(strategy_overrides or {})
        self.feature_overrides = dict(feature_overrides or {})
        self.automatic_mode_overrides = dict(automatic_mode_overrides or {})
        self.automatic_strategy_overrides = dict(automatic_strategy_overrides or {})
        self.automatic_strategy_mode_overrides = {
            str(strategy).strip().lower(): {
                str(mode_name).strip().lower(): overrides
                for mode_name, overrides in mode_overrides.items()
            }
            for strategy, mode_overrides in (automatic_strategy_mode_overrides or {}).items()
        }
        self.automatic_feature_overrides = dict(automatic_feature_overrides or {})
        self.automatic_surface_speed_m_per_min = float(automatic_surface_speed_m_per_min)
        self.automatic_chip_load = float(automatic_chip_load)
        self.automatic_plunge_ratio = float(automatic_plunge_ratio)
        self.automatic_stepdown_ratio = float(automatic_stepdown_ratio)
        self.automatic_lead_ratio = float(automatic_lead_ratio)
        self.material_factor = float(material_factor)

    def resolve(self, request: OperationRequest, tool: ToolDefinition) -> MachiningParameters:
        computed_values = self._get_automatic_defaults(request, tool) if self.mode == "automatic" else {}
        tool_values = self._tool_defaults(tool)
        merged = self._merge_parameter_values(
            computed_values,
            tool_values,
            self.base_overrides,
            self.strategy_overrides.get(request.strategy),
            self.feature_overrides.get(request.feature_type),
        )

        cut_feed = float(merged.get("cut_feed", 300.0))
        plunge_feed = float(merged.get("plunge_feed", 120.0))
        spindle_speed = merged.get("spindle_speed")
        max_stepdown = float(merged.get("max_stepdown", max(request.depth, 0.1) or 4.0))
        stepover_ratio = float(merged.get("stepover_ratio", 0.6))
        lead_in_distance = float(merged.get("lead_in_distance", 0.0))
        lead_out_distance = float(merged.get("lead_out_distance", 0.0))

        return MachiningParameters(
            tool=tool,
            parameter_mode=self.mode,
            cut_feed=cut_feed,
            plunge_feed=plunge_feed,
            spindle_speed=float(spindle_speed) if spindle_speed is not None else None,
            max_stepdown=max(max_stepdown, 1e-6),
            stepover_ratio=max(stepover_ratio, 1e-6),
            lead_in_distance=max(lead_in_distance, 0.0),
            lead_out_distance=max(lead_out_distance, 0.0),
        )

    def _get_automatic_defaults(self, request: OperationRequest, tool: ToolDefinition) -> dict[str, float]:
        automatic_values = self._merge_automatic_parameter_values(
            AutomaticParameterOverrides(
                surface_speed_m_per_min=self.automatic_surface_speed_m_per_min,
                chip_load=self.automatic_chip_load,
                plunge_ratio=self.automatic_plunge_ratio,
                stepdown_ratio=self.automatic_stepdown_ratio,
                lead_ratio=self.automatic_lead_ratio,
                material_factor=self.material_factor,
            ),
            self.automatic_mode_overrides.get(request.operation_mode) if request.operation_mode else None,
            self.automatic_strategy_overrides.get(request.strategy),
            self.automatic_feature_overrides.get(request.feature_type),
            self._get_strategy_mode_override(request),
        )

        diameter = max(float(tool.diameter), 0.1)
        surface_speed = float(automatic_values.get("surface_speed_m_per_min", self.automatic_surface_speed_m_per_min))
        chip_load = float(automatic_values.get("chip_load", self.automatic_chip_load))
        plunge_ratio = float(automatic_values.get("plunge_ratio", self.automatic_plunge_ratio))
        stepdown_ratio = float(automatic_values.get("stepdown_ratio", self.automatic_stepdown_ratio))
        lead_ratio = float(automatic_values.get("lead_ratio", self.automatic_lead_ratio))
        material_factor = float(automatic_values.get("material_factor", self.material_factor))
        stepover_ratio_override = automatic_values.get("stepover_ratio")

        spindle_speed = (surface_speed * 1000.0 * material_factor) / (np.pi * diameter)
        flute_count = max(int(tool.flute_count), 1)

        if tool.tool_type == "drill":
            drill_chip_load = max(chip_load * 0.5 * material_factor, 0.001)
            cut_feed = spindle_speed * drill_chip_load
            plunge_feed = cut_feed
            drill_stepdown_ratio = stepdown_ratio if "stepdown_ratio" in automatic_values else 1.5
            max_stepdown = max(request.depth or diameter, diameter * drill_stepdown_ratio)
            stepover_ratio = (
                float(stepover_ratio_override)
                if stepover_ratio_override is not None
                else (tool.stepover_ratio if tool.stepover_ratio is not None else 1.0)
            )
            lead_distance = 0.0
        else:
            milling_chip_load = max(chip_load * material_factor, 0.001)
            cut_feed = spindle_speed * flute_count * milling_chip_load
            plunge_feed = cut_feed * plunge_ratio
            max_stepdown = diameter * stepdown_ratio
            stepover_ratio = (
                float(stepover_ratio_override)
                if stepover_ratio_override is not None
                else (tool.stepover_ratio if tool.stepover_ratio is not None else 0.6)
            )
            lead_distance = diameter * lead_ratio

        return {
            "spindle_speed": spindle_speed,
            "cut_feed": cut_feed,
            "plunge_feed": plunge_feed,
            "max_stepdown": max_stepdown,
            "stepover_ratio": stepover_ratio,
            "lead_in_distance": lead_distance,
            "lead_out_distance": lead_distance,
        }

    def _get_strategy_mode_override(self, request: OperationRequest) -> Optional[AutomaticParameterOverrides]:
        if request.operation_mode is None:
            return None
        strategy_modes = self.automatic_strategy_mode_overrides.get(request.strategy)
        if strategy_modes is None:
            return None
        return strategy_modes.get(request.operation_mode)

    @staticmethod
    def _tool_defaults(tool: ToolDefinition) -> dict[str, float]:
        defaults: dict[str, float] = {}
        for key in ("cut_feed", "plunge_feed", "spindle_speed", "max_stepdown", "stepover_ratio"):
            value = getattr(tool, key)
            if value is not None:
                defaults[key] = float(value)
        return defaults

    @staticmethod
    def _merge_parameter_values(*sources: Any) -> dict[str, float]:
        merged: dict[str, float] = {}
        for source in sources:
            if source is None:
                continue
            if isinstance(source, ParameterOverrides):
                source_dict = source.__dict__
            else:
                source_dict = source
            for key, value in source_dict.items():
                if value is not None:
                    merged[key] = float(value)
        return merged

    @staticmethod
    def _merge_automatic_parameter_values(*sources: Any) -> dict[str, float]:
        merged: dict[str, float] = {}
        for source in sources:
            if source is None:
                continue
            if isinstance(source, AutomaticParameterOverrides):
                source_dict = source.__dict__
            else:
                source_dict = source
            for key, value in source_dict.items():
                if value is not None:
                    merged[key] = float(value)
        return merged


class AutoToolpathPlanner:
    """Build a first 2.5D toolpath plan from features and optional perimeter loops."""

    def __init__(
        self,
        working_plane_normal: tuple[float, float, float] = (0.0, 0.0, 1.0),
        safe_z_offset: float = 5.0,
        clearance_z: float = 1.0,
        plunge_feed: float = 120.0,
        cut_feed: float = 300.0,
        max_stepdown: float = 4.0,
        tool_diameter: float = 6.0,
        stepover_ratio: float = 0.6,
        parameter_mode: str = "manual",
        lead_in_distance: float = 0.0,
        lead_out_distance: float = 0.0,
        profile_stock_allowance: float = 0.0,
        roughing_distance: float = 1.0,
        enable_perimeter_profile_fallback: bool = False,
        profile_rough_only: bool = False,
        piece_roughing_only: bool = False,
        tool_manager: Optional[ToolManager] = None,
        parameter_resolver: Optional[MachiningParameterResolver] = None,
        operation_priority: Optional[dict[str, int]] = None,
    ) -> None:
        axis = np.array(working_plane_normal, dtype=float)
        norm = np.linalg.norm(axis)
        if norm == 0:
            axis = np.array([0.0, 0.0, 1.0], dtype=float)
            norm = 1.0
        self.working_plane_normal = axis / norm
        self.safe_z_offset = float(safe_z_offset)
        self.clearance_z = float(clearance_z)
        self.plunge_feed = float(plunge_feed)
        self.cut_feed = float(cut_feed)
        self.max_stepdown = float(max_stepdown)
        self.tool_diameter = float(tool_diameter)
        self.stepover_ratio = float(stepover_ratio)
        self.lead_in_distance = float(lead_in_distance)
        self.lead_out_distance = float(lead_out_distance)
        self.profile_stock_allowance = float(profile_stock_allowance)
        self.roughing_distance = float(roughing_distance)
        self.enable_perimeter_profile_fallback = bool(enable_perimeter_profile_fallback)
        self.profile_rough_only = bool(profile_rough_only)
        self.piece_roughing_only = bool(piece_roughing_only)
        self.tool_manager = tool_manager or ToolManager(
            ToolManager.build_default_tools(self.tool_diameter),
            preferred_diameter=self.tool_diameter,
        )
        if parameter_mode == "automatic":
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
        self.parameter_resolver = parameter_resolver or MachiningParameterResolver(
            mode=parameter_mode,
            base_overrides=base_overrides,
        )
        self.operation_priority = dict(operation_priority or {
            "drilling": 10,
            "cavity_clearing": 20,
            "slot_milling": 30,
            "2p5d_profile": 40,
        })

    def generate(
        self,
        features: Iterable[Any],
        perimeter_wires: Optional[Iterable[Any]] = None,
        perimeter_polylines: Optional[Iterable[Iterable[tuple[float, float, float]]]] = None,
        *,
        piece_top_projection: Optional[float] = None,
        piece_bottom_projection: Optional[float] = None,
    ) -> ToolpathPlan:
        """Generate a neutral toolpath plan from extracted semantic data.

        Args:
            features: Extracted features (automatic and/or manual in futuro).
            perimeter_wires: OCC wires from contour extraction.
            perimeter_polylines: Optional pre-sampled loops for tests or custom input.
        """
        feature_list = list(features)
        warnings: list[str] = []

        loops = [list(loop) for loop in (perimeter_polylines or [])]
        profile_geometry_source = "perimeter_polyline_fallback" if loops else "perimeter_loop"
        if perimeter_wires:
            try:
                loops.extend(self.wires_to_polylines(perimeter_wires))
                profile_geometry_source = "perimeter_occ_wire"
            except Exception as exc:  # pragma: no cover - defensive on OCC runtime
                warnings.append(f"Perimeter wire conversion failed: {exc}")

        loop_bottom_projection, loop_top_projection = self._infer_projection_bounds_from_loops(loops)
        if self.piece_roughing_only:
            top_projection = loop_top_projection if piece_top_projection is None else float(piece_top_projection)
            target_projection = loop_bottom_projection if piece_bottom_projection is None else float(piece_bottom_projection)
            if target_projection > top_projection:
                top_projection, target_projection = target_projection, top_projection
        else:
            top_projection = self._infer_top_projection(feature_list)
            target_projection = None
        safe_projection = top_projection + self.safe_z_offset

        operations: list[ToolpathOperation] = []
        if self.piece_roughing_only:
            warnings.append(
                "Piece rough only mode enabled: semantic feature operations are disabled and roughing is derived only from the part perimeter"
            )
            if loops:
                operations.extend(
                    self._build_piece_roughing_ops(
                        loops,
                        safe_projection,
                        top_projection,
                        target_projection=target_projection,
                        geometry_source=profile_geometry_source,
                    )
                )
            else:
                warnings.append("Piece rough only mode enabled but no perimeter geometry is available")
        elif not self.profile_rough_only:
            operations.extend(self._build_drilling_ops(feature_list, safe_projection))
            slot_ops, slot_warnings = self._build_slot_ops(feature_list, safe_projection)
            operations.extend(slot_ops)
            warnings.extend(slot_warnings)
            cavity_ops, cavity_warnings = self._build_cavity_ops(feature_list, safe_projection)
            operations.extend(cavity_ops)
            warnings.extend(cavity_warnings)
        else:
            warnings.append(
                "Profile rough only mode enabled: drilling, slot milling, cavity clearing, and profile finishing are disabled"
            )

        if loops and not self.piece_roughing_only:
            if operations and not self.enable_perimeter_profile_fallback:
                warnings.append(
                    "Perimeter profile fallback skipped: silhouette-derived profile cuts are disabled when semantic machining operations already exist"
                )
            else:
                operations.extend(
                    self._build_profile_ops(
                        loops,
                        safe_projection,
                        top_projection,
                        geometry_source=profile_geometry_source,
                    )
                )
        elif self.profile_rough_only:
            warnings.append("Profile rough only mode enabled but no perimeter geometry is available")
        operations = self._order_operations(operations)

        if not operations and not warnings:
            warnings.append("No operations generated: insufficient features/perimeter data")

        return ToolpathPlan(
            working_plane_normal=self._to_point(self.working_plane_normal),
            safe_projection=safe_projection,
            operations=operations,
            warnings=warnings,
        )

    def _build_piece_roughing_ops(
        self,
        perimeter_loops: Iterable[Iterable[tuple[float, float, float]]],
        safe_projection: float,
        top_projection: float,
        *,
        target_projection: float,
        geometry_source: str,
    ) -> list[ToolpathOperation]:
        normalized_loops = self._normalize_planar_loops(perimeter_loops)
        if not normalized_loops:
            return []

        outer_loop = normalized_loops[0]
        region = self._build_planar_region_from_loops([outer_loop])
        if region is None:
            return []

        depth = max(0.0, float(top_projection - target_projection))

        request = OperationRequest(
            strategy="cavity_clearing",
            feature_type="piece",
            operation_mode="roughing",
            depth=depth,
            span_u=region.span_u,
            span_v=region.span_v,
        )
        parameters = self._resolve_operation_parameters(request)
        if parameters is None:
            return []

        roughing_passes = self._build_depth_pass_projections(
            top_projection,
            target_projection,
            max_stepdown=parameters.max_stepdown,
        )
        cut_paths, clearing_style = self._build_cavity_roughing_paths(region, parameters)
        if not cut_paths:
            return []

        motions: list[MotionCommand] = []
        for pass_projection in roughing_passes:
            for path_index, cut_path in enumerate(cut_paths):
                ordered_path = cut_path if path_index % 2 == 0 else list(reversed(cut_path))
                projected_path = [self._point_at_projection(point, pass_projection) for point in ordered_path]
                motions.extend(
                    self._build_milling_path_motions(
                        projected_path,
                        top_projection=top_projection,
                        safe_projection=safe_projection,
                        parameters=parameters,
                    )
                )

        metadata = {
            "piece_based": True,
            "depth": round(depth, 4),
            "top_projection": round(float(top_projection), 4),
            "target_projection": round(float(target_projection), 4),
            "input_loop_count": len(normalized_loops),
            "pass_count": len(roughing_passes),
            "path_count": len(cut_paths),
            "line_count": len(cut_paths),
            "clearing_style": clearing_style,
            "span_u": round(region.span_u, 4),
            "span_v": round(region.span_v, 4),
        }
        metadata.update(
            self._build_feature_metadata(
                MachiningFeatureContext("piece_roughing", "piece_shadow", geometry_source)
            )
        )
        metadata.update(self._build_parameter_metadata(parameters, request))
        return [
            ToolpathOperation(
                op_id="piece_rough_0",
                strategy="cavity_clearing",
                feature_type="piece",
                motions=motions,
                metadata=metadata,
            )
        ]

    def _offset_planar_loop_2d(
        self,
        loop: list[np.ndarray],
        offset_distance: float,
    ) -> list[np.ndarray]:
        """Expand a closed polygon outward in the XY plane by *offset_distance*.

        Uses a simple per-vertex normal-offset approach. For convex regions
        this produces a conservative expansion. For concave regions the
        result may self-intersect; we rely on the cavity-clearing path
        builder to handle that gracefully.
        """
        if offset_distance <= 0.0 or len(loop) < 3:
            return loop

        pts = [np.array(p, dtype=float) for p in loop]
        n = len(pts)
        # remove duplicate endpoint
        if np.linalg.norm(pts[0] - pts[-1]) < 1e-10:
            pts.pop()
            n = len(pts)
        if n < 3:
            return loop

        offset_pts: list[np.ndarray] = []
        for i in range(n):
            prev = pts[(i - 1) % n]
            curr = pts[i]
            next_ = pts[(i + 1) % n]

            e1 = curr - prev
            e2 = next_ - curr
            n1 = np.array([-e1[1], e1[0], 0.0])
            n2 = np.array([-e2[1], e2[0], 0.0])
            len1 = float(np.linalg.norm(n1))
            len2 = float(np.linalg.norm(n2))
            if len1 < 1e-12 or len2 < 1e-12:
                offset_pts.append(curr.copy())
                continue
            n1 /= len1
            n2 /= len2

            bisector = n1 + n2
            bis_len = float(np.linalg.norm(bisector))
            if bis_len < 1e-12:
                offset_pts.append(curr + n1 * float(offset_distance))
            else:
                bisector /= bis_len
                dot_val = float(np.dot(n1, bisector))
                if abs(dot_val) < 1e-12:
                    scale = float(offset_distance)
                else:
                    scale = float(offset_distance) / dot_val
                offset_pts.append(curr + bisector * scale)

        if np.linalg.norm(offset_pts[0] - offset_pts[-1]) > 1e-10:
            offset_pts.append(offset_pts[0].copy())

        return offset_pts

    def _build_waterline_roughing_ops(
        self,
        brep_shape: Any,
        safe_projection: float,
        top_projection: float,
        *,
        target_projection: float,
        roughing_distance: float = 1.0,
        geometry_source: str = "waterline_2d_offset",
    ) -> list[ToolpathOperation]:
        """Generate Z-level waterline roughing toolpaths from a 3D BRep model.

        For each Z pass: section the *original* shape, then expand each
        section contour outward by *roughing_distance* in XY.  This is
        faster and more robust than computing a full 3D offset body.
        """
        depth = max(0.0, float(top_projection - target_projection))
        if depth <= 0.0:
            return []

        request = OperationRequest(
            strategy="cavity_clearing",
            feature_type="piece",
            operation_mode="roughing",
            depth=depth,
        )
        parameters = self._resolve_operation_parameters(request)
        if parameters is None:
            return []

        # --- bounding-box of original shape (used for outer boundary) -------
        try:
            from OCP.BRepBndLib import BRepBndLib
            from OCP.Bnd import Bnd_Box

            bbox = Bnd_Box()
            BRepBndLib.Add_s(brep_shape, bbox)
            x_min, y_min, z_min, x_max, y_max, z_max = bbox.Get()
            bbox_center = np.array([(x_min + x_max) * 0.5, (y_min + y_max) * 0.5, (z_min + z_max) * 0.5])
            bbox_span_u = float(x_max - x_min)
            bbox_span_v = float(y_max - y_min)
        except Exception:
            return []

        pass_projections = self._build_depth_pass_projections(
            top_projection, target_projection,
            max_stepdown=parameters.max_stepdown,
        )
        if not pass_projections:
            return []

        operations: list[ToolpathOperation] = []
        for pass_idx, pass_proj in enumerate(pass_projections):
            section_loops = self._section_brep_at_projection(brep_shape, pass_proj)
            if not section_loops:
                continue

            boundary_loops = self._normalize_planar_loops(section_loops)
            if not boundary_loops:
                continue

            # Expand each inner contour by roughing_distance in XY
            expanded_loops: list[list[np.ndarray]] = []
            for loop in boundary_loops:
                expanded = self._offset_planar_loop_2d(loop, float(roughing_distance))
                expanded_loops.append(expanded)

            # Outer boundary from bbox (expanded by roughing_distance)
            margin = float(roughing_distance)
            outer_polygon = [
                (x_min - margin, y_min - margin, pass_proj),
                (x_max + margin, y_min - margin, pass_proj),
                (x_max + margin, y_max + margin, pass_proj),
                (x_min - margin, y_max + margin, pass_proj),
            ]
            outer_loop = [np.array(p, dtype=float) for p in outer_polygon]
            if np.linalg.norm(outer_loop[0] - outer_loop[-1]) > 1e-6:
                outer_loop.append(outer_loop[0])

            all_loops = [outer_loop] + expanded_loops

            basis_u, basis_v = self._get_working_plane_basis()
            region = PlanarRegion(
                center=bbox_center,
                basis_u=basis_u,
                basis_v=basis_v,
                span_u=float(x_max - x_min) + margin * 2.0,
                span_v=float(y_max - y_min) + margin * 2.0,
                projection=pass_proj,
                boundary_loops=all_loops,
            )

            cut_paths, clearing_style = self._build_cavity_roughing_paths(region, parameters, allow_occ_offsets=False)
            if not cut_paths:
                continue

            motions: list[MotionCommand] = []
            for path_index, cut_path in enumerate(cut_paths):
                ordered_path = cut_path if path_index % 2 == 0 else list(reversed(cut_path))
                projected_path = [self._point_at_projection(point, pass_proj) for point in ordered_path]
                motions.extend(
                    self._build_milling_path_motions(
                        projected_path,
                        top_projection=top_projection,
                        safe_projection=safe_projection,
                        parameters=parameters,
                    )
                )

            metadata = {
                "waterline_based": True,
                "roughing_distance": round(float(roughing_distance), 4),
                "pass_index": pass_idx,
                "depth": round(depth, 4),
                "top_projection": round(float(top_projection), 4),
                "target_projection": round(float(target_projection), 4),
                "pass_count": len(pass_projections),
                "path_count": len(cut_paths),
                "clearing_style": clearing_style,
                "section_loop_count": len(section_loops),
                "span_u": round(float(x_max - x_min), 4),
                "span_v": round(float(y_max - y_min), 4),
            }
            metadata.update(
                self._build_feature_metadata(
                    MachiningFeatureContext("piece_roughing", "waterline_offset", geometry_source)
                )
            )
            metadata.update(self._build_parameter_metadata(parameters, request))

            operations.append(
                ToolpathOperation(
                    op_id=f"waterline_rough_{pass_idx}",
                    strategy="cavity_clearing",
                    feature_type="piece",
                    motions=motions,
                    metadata=metadata,
                )
            )

        return operations

    def _section_brep_at_projection(
        self,
        shape: Any,
        projection: float,
    ) -> list[list[tuple[float, float, float]]]:
        """Section an OCC shape at a given projection using BRep + BBox classification.

        1. BRepAlgoAPI_Section → TopoDS_Wire chiusi
        2. Classifica per BBox containment (outer / holes / nested groups)
        3. Converte ogni wire in polilinea campionata
        4. Caches classified OCC wires in ``self._section_wire_groups[projection]``
        5. Fallback su mesh slicing se OCC section fallisce

        Returns list of polylines: [outer, hole1, hole2, ...] per gruppo,
        con l'outer sempre per primo.
        """
        from antcam.slicer import (
            _brep_section_get_wires,
            _brep_section_classify_wires,
            _brep_wire_to_polyline,
            _tessellate_brep,
            _slice_mesh_at_z,
        )

        normal = self.working_plane_normal

        # Cached wire groups for direct OCC offset in roughing generator
        wire_cache: dict[float, list[tuple[Any, list[Any]]]] = getattr(
            self, '_section_wire_groups', {},
        )

        # Primary: OCC BRep section
        try:
            wires = _brep_section_get_wires(shape, projection, normal)
            if wires:
                groups = _brep_section_classify_wires(wires)
                wire_cache[projection] = groups
                self._section_wire_groups = wire_cache
                polylines: list[list[tuple[float, float, float]]] = []
                for outer_wire, hole_wires in groups:
                    pts = _brep_wire_to_polyline(outer_wire)
                    if len(pts) >= 3:
                        polylines.append(pts)
                    for hw in hole_wires:
                        pts = _brep_wire_to_polyline(hw)
                        if len(pts) >= 3:
                            polylines.append(pts)
                if polylines:
                    return polylines
        except Exception:
            pass

        # Fallback: mesh slicing (wire cache not populated)
        cache = getattr(self, '_slice_mesh_cache', {})
        cache_key = id(shape)
        if cache_key not in cache:
            try:
                cache[cache_key] = _tessellate_brep(shape, 0.02)
            except Exception:
                cache[cache_key] = None
            self._slice_mesh_cache = cache
        mesh = cache.get(cache_key)

        if mesh is not None and not mesh.is_empty:
            try:
                return _slice_mesh_at_z(mesh, projection, normal)
            except Exception:
                pass

        return []

    def _offset_occ_wire_direct(
        self,
        wire: Any,
        offset_distance: float,
    ) -> list[Any]:
        """Offset a TopoDS_Wire directly via BRepOffsetAPI_MakeOffset.

        Bypasses polyline roundtrip (which degrades geometry).
        Returns list of offset TopoDS_Wire.
        """
        try:
            from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
            from OCP.GeomAbs import GeomAbs_Intersection
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopAbs import TopAbs_WIRE
            from OCP.TopoDS import TopoDS
        except Exception:
            return []

        offset_builder = BRepOffsetAPI_MakeOffset()
        offset_builder.Init(GeomAbs_Intersection)
        offset_builder.AddWire(wire)
        offset_builder.Perform(float(offset_distance), 0.0)
        offset_shape = offset_builder.Shape()
        if offset_shape.IsNull():
            return []

        wires: list[Any] = []
        exp_w = TopExp_Explorer(offset_shape, TopAbs_WIRE)
        while exp_w.More():
            wires.append(TopoDS.Wire_s(exp_w.Current()))
            exp_w.Next()
        return wires

    def _resolve_operation_parameters(self, request: OperationRequest) -> Optional[MachiningParameters]:
        tool = self.tool_manager.select_tool(request)
        if tool is None:
            return None
        return self.parameter_resolver.resolve(request, tool)

    def _build_parameter_metadata(
        self,
        parameters: MachiningParameters,
        request: Optional[OperationRequest] = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "tool_id": parameters.tool.tool_id,
            "tool_name": parameters.tool.name,
            "tool_type": parameters.tool.tool_type,
            "tool_diameter": round(float(parameters.tool.diameter), 4),
            "parameter_mode": parameters.parameter_mode,
            "cut_feed": round(float(parameters.cut_feed), 4),
            "plunge_feed": round(float(parameters.plunge_feed), 4),
            "max_stepdown": round(float(parameters.max_stepdown), 4),
            "stepover_ratio": round(float(parameters.stepover_ratio), 4),
            "lead_in_distance": round(float(parameters.lead_in_distance), 4),
            "lead_out_distance": round(float(parameters.lead_out_distance), 4),
        }
        if request is not None and request.operation_mode is not None:
            metadata["operation_mode"] = request.operation_mode
        if parameters.tool.flute_length is not None:
            metadata["tool_flute_length"] = round(float(parameters.tool.flute_length), 4)
        if parameters.tool.stickout is not None:
            metadata["tool_stickout"] = round(float(parameters.tool.stickout), 4)
        if parameters.tool.tool_material is not None:
            metadata["tool_material"] = parameters.tool.tool_material
        if parameters.tool.compatible_materials is not None:
            metadata["compatible_materials"] = list(parameters.tool.compatible_materials)
        if parameters.tool.supported_modes is not None:
            metadata["supported_modes"] = list(parameters.tool.supported_modes)
        if parameters.spindle_speed is not None:
            metadata["spindle_speed"] = round(float(parameters.spindle_speed), 4)
        return metadata

    @staticmethod
    def _build_feature_metadata(context: MachiningFeatureContext) -> dict[str, Any]:
        return {
            "machining_class": context.machining_class,
            "feature_subtype": context.feature_subtype,
            "geometry_source": context.geometry_source,
        }

    def _classify_feature_context(
        self,
        feature: Any,
        *,
        geometry_source: str,
    ) -> MachiningFeatureContext:
        feature_type = str(getattr(feature, "type", "") or "").strip().lower()
        if feature_type == "hole_group":
            return MachiningFeatureContext("hole_making", "hole_group", geometry_source)
        if feature_type == "slot":
            through = bool(getattr(feature, "props", {}).get("through", False))
            return MachiningFeatureContext(
                "slot_milling",
                "through_slot" if through else "closed_slot",
                geometry_source,
            )
        if feature_type == "pocket":
            return MachiningFeatureContext("pocket_milling", "closed_pocket", geometry_source)
        if feature_type == "opening":
            return MachiningFeatureContext("pocket_milling", "open_pocket", geometry_source)
        if feature_type == "step":
            return MachiningFeatureContext("pocket_milling", "step_face", geometry_source)
        return MachiningFeatureContext("general_milling", feature_type or "unknown", geometry_source)

    @staticmethod
    def _build_perimeter_context(*, closed: bool, geometry_source: str) -> MachiningFeatureContext:
        return MachiningFeatureContext(
            "profile_milling",
            "closed_profile" if closed else "open_profile",
            geometry_source,
        )

    def _order_operations(self, operations: list[ToolpathOperation]) -> list[ToolpathOperation]:
        mode_priority = {
            "drilling": 10,
            "roughing": 20,
            "finishing": 30,
        }
        ordered = sorted(
            operations,
            key=lambda operation: (
                self.operation_priority.get(operation.strategy, 999),
                mode_priority.get(str(operation.metadata.get("operation_mode", "")), 99),
                operation.op_id,
            ),
        )
        for index, operation in enumerate(ordered):
            operation.metadata.setdefault("sequence", index)
        return ordered

    def _build_milling_path_motions(
        self,
        path_points: list[np.ndarray],
        top_projection: float,
        safe_projection: float,
        parameters: MachiningParameters,
    ) -> list[MotionCommand]:
        if len(path_points) < 2:
            return []

        start_direction = self._get_path_direction(path_points[0], path_points[1])
        end_direction = self._get_path_direction(path_points[-2], path_points[-1])
        lead_start = self._offset_point(path_points[0], -start_direction, parameters.lead_in_distance)
        lead_end = self._offset_point(path_points[-1], end_direction, parameters.lead_out_distance)
        cut_projection = float(np.dot(path_points[0], self.working_plane_normal))

        safe_start = self._point_at_projection(lead_start, safe_projection)
        clearance_start = self._point_at_projection(lead_start, top_projection + self.clearance_z)
        lead_start_cut = self._point_at_projection(lead_start, cut_projection)

        motions = [
            MotionCommand(move="rapid", point=self._to_point(safe_start)),
            MotionCommand(move="rapid", point=self._to_point(clearance_start)),
            MotionCommand(move="linear", point=self._to_point(lead_start_cut), feed=parameters.plunge_feed),
        ]

        if np.linalg.norm(lead_start_cut - path_points[0]) > 1e-9:
            motions.append(MotionCommand(move="linear", point=self._to_point(path_points[0]), feed=parameters.cut_feed))

        motions.extend(
            MotionCommand(move="linear", point=self._to_point(point), feed=parameters.cut_feed)
            for point in path_points[1:]
        )

        lead_end_cut = self._point_at_projection(lead_end, cut_projection)
        if np.linalg.norm(lead_end_cut - path_points[-1]) > 1e-9:
            motions.append(MotionCommand(move="linear", point=self._to_point(lead_end_cut), feed=parameters.cut_feed))

        safe_end = self._point_at_projection(lead_end_cut, safe_projection)
        motions.append(MotionCommand(move="rapid", point=self._to_point(safe_end)))
        return motions

    def _get_path_direction(self, start_point: np.ndarray, end_point: np.ndarray) -> np.ndarray:
        vector = np.array(end_point, dtype=float) - np.array(start_point, dtype=float)
        projected = vector - self.working_plane_normal * float(np.dot(vector, self.working_plane_normal))
        norm = float(np.linalg.norm(projected))
        if norm <= 1e-9:
            return self._get_working_plane_basis()[0]
        return projected / norm

    @staticmethod
    def _offset_point(point: np.ndarray, direction: np.ndarray, distance: float) -> np.ndarray:
        if distance <= 1e-9:
            return np.array(point, dtype=float)
        return np.array(point, dtype=float) + np.array(direction, dtype=float) * float(distance)

    def _build_drilling_ops(self, features: Iterable[Any], safe_projection: float) -> list[ToolpathOperation]:
        operations: list[ToolpathOperation] = []

        for idx, feature in enumerate(features):
            if getattr(feature, "type", "") != "hole_group":
                continue
            holes = list(getattr(feature, "holes", []))
            if not holes:
                continue
            feature_context = self._classify_feature_context(feature, geometry_source="hole_centers")

            through = bool(feature.props.get("through", False))
            group_depth = float(feature.props.get("depth", 0.0) or 0.0)
            diameter = float(feature.props.get("diameter", 0.0) or 0.0)
            breakthrough = 0.5 if through else 0.0
            request = OperationRequest(
                strategy="drilling",
                feature_type="hole_group",
                operation_mode="drilling",
                depth=group_depth + breakthrough,
                diameter=diameter,
                through=through,
            )
            parameters = self._resolve_operation_parameters(request)
            if parameters is None:
                continue

            ordered_holes = sorted(
                holes,
                key=lambda h: tuple(float(v) for v in h.props.get("center", (0.0, 0.0, 0.0))),
            )

            motions: list[MotionCommand] = []
            for hole in ordered_holes:
                center = np.array(hole.props.get("center", (0.0, 0.0, 0.0)), dtype=float)
                proj_center = float(np.dot(center, self.working_plane_normal))
                safe_point = self._point_at_projection(center, safe_projection)
                clearance_point = self._point_at_projection(center, proj_center + self.clearance_z)
                target_projection = proj_center - (group_depth + breakthrough)
                pass_projections = self._build_depth_pass_projections(
                    proj_center,
                    target_projection,
                    max_stepdown=parameters.max_stepdown,
                )

                motions.append(MotionCommand(move="rapid", point=self._to_point(safe_point)))
                motions.append(MotionCommand(move="rapid", point=self._to_point(clearance_point)))
                for pass_idx, pass_projection in enumerate(pass_projections):
                    target_point = self._point_at_projection(center, pass_projection)
                    motions.append(
                        MotionCommand(move="linear", point=self._to_point(target_point), feed=parameters.plunge_feed)
                    )
                    if pass_idx < len(pass_projections) - 1:
                        motions.append(MotionCommand(move="rapid", point=self._to_point(clearance_point)))
                motions.append(MotionCommand(move="rapid", point=self._to_point(safe_point)))

            first_center = np.array(ordered_holes[0].props.get("center", (0.0, 0.0, 0.0)), dtype=float)
            first_projection = float(np.dot(first_center, self.working_plane_normal))
            pass_count = len(
                self._build_depth_pass_projections(
                    first_projection,
                    first_projection - (group_depth + breakthrough),
                    max_stepdown=parameters.max_stepdown,
                )
            )

            metadata = {
                "hole_count": len(ordered_holes),
                "diameter": round(diameter, 4),
                "depth": round(group_depth, 4),
                "through": through,
                "pass_count": pass_count,
            }
            metadata.update(self._build_feature_metadata(feature_context))
            metadata.update(self._build_parameter_metadata(parameters, request))

            operations.append(
                ToolpathOperation(
                    op_id=f"drill_{idx}",
                    strategy="drilling",
                    feature_type="hole_group",
                    motions=motions,
                    metadata=metadata,
                )
            )

        return operations

    def _build_slot_ops(
        self,
        features: Iterable[Any],
        safe_projection: float,
    ) -> tuple[list[ToolpathOperation], list[str]]:
        operations: list[ToolpathOperation] = []
        warnings: list[str] = []

        for idx, feature in enumerate(features):
            if getattr(feature, "type", "") != "slot":
                continue

            operations_for_feature = self._build_slot_operations(feature, idx, safe_projection)
            if not operations_for_feature:
                warnings.append(f"Slot feature {idx} is missing a usable in-plane axis; skipping slot milling")
                continue

            operations.extend(operations_for_feature)

        return operations, warnings

    def _build_slot_operations(
        self,
        feature: Any,
        index: int,
        safe_projection: float,
    ) -> list[ToolpathOperation]:
        center = feature.props.get("center") if hasattr(feature, "props") else None
        axis = feature.props.get("axis") if hasattr(feature, "props") else None
        if center is None or axis is None:
            return []

        center_np = np.array(center, dtype=float)
        axis_np = np.array(axis, dtype=float)
        length_direction = self._get_in_plane_direction(axis_np)
        if length_direction is None:
            return []
        width_direction = self._get_slot_width_direction(length_direction)

        width = float(feature.props.get("width", 0.0) or 0.0)
        length = float(feature.props.get("length", 0.0) or 0.0)
        depth = float(feature.props.get("depth", 0.0) or 0.0)
        through = bool(feature.props.get("through", False))
        if width <= 0.0 or length <= 0.0 or depth <= 0.0:
            return []
        feature_context = self._classify_feature_context(feature, geometry_source="slot_axis")

        start_point, end_point = self._get_slot_centerline_endpoints(center_np, length_direction, width, length)
        center_projection = float(np.dot(center_np, self.working_plane_normal))
        breakthrough = 0.5 if through else 0.0
        target_projection = center_projection - (depth + breakthrough)
        operations: list[ToolpathOperation] = []

        roughing_request = OperationRequest(
            strategy="slot_milling",
            feature_type="slot",
            operation_mode="roughing",
            depth=depth + (0.5 if through else 0.0),
            width=width,
            length=length,
            through=through,
        )
        roughing_parameters = self._resolve_operation_parameters(roughing_request)
        if roughing_parameters is not None:
            roughing_passes = self._build_depth_pass_projections(
                center_projection,
                target_projection,
                max_stepdown=roughing_parameters.max_stepdown,
            )
            roughing_paths, roughing_style = self._build_slot_roughing_paths(
                start_point,
                end_point,
                length_direction,
                width_direction,
                width,
                length,
                depth,
                roughing_parameters.tool.diameter,
                roughing_parameters.stepover_ratio,
            )
            roughing_motions: list[MotionCommand] = []
            for pass_projection in roughing_passes:
                for path_index, roughing_path in enumerate(roughing_paths):
                    ordered_path = roughing_path if path_index % 2 == 0 else list(reversed(roughing_path))
                    projected_path = [self._point_at_projection(point, pass_projection) for point in ordered_path]
                    roughing_motions.extend(
                        self._build_milling_path_motions(
                            projected_path,
                            top_projection=center_projection,
                            safe_projection=safe_projection,
                            parameters=roughing_parameters,
                        )
                    )

            roughing_metadata = {
                "width": round(width, 4),
                "length": round(length, 4),
                "depth": round(depth, 4),
                "through": through,
                "pass_count": len(roughing_passes),
                "roughing_path_count": len(roughing_paths),
                "roughing_style": roughing_style,
            }
            roughing_metadata.update(self._build_feature_metadata(feature_context))
            roughing_metadata.update(self._build_parameter_metadata(roughing_parameters, roughing_request))
            operations.append(
                ToolpathOperation(
                    op_id=f"slot_{index}_rough",
                    strategy="slot_milling",
                    feature_type="slot",
                    motions=roughing_motions,
                    metadata=roughing_metadata,
                )
            )

        finishing_request = OperationRequest(
            strategy="slot_milling",
            feature_type="slot",
            operation_mode="finishing",
            depth=depth + (0.5 if through else 0.0),
            width=width,
            length=length,
            through=through,
        )
        finishing_parameters = self._resolve_operation_parameters(finishing_request)
        if finishing_parameters is None:
            return operations

        finishing_paths = self._build_slot_finishing_paths(
            start_point,
            end_point,
            width_direction,
            width,
            finishing_parameters.tool.diameter,
        )
        if not finishing_paths:
            return operations

        finishing_passes = self._build_depth_pass_projections(
            center_projection,
            target_projection,
            max_stepdown=finishing_parameters.max_stepdown,
        )
        finishing_motions: list[MotionCommand] = []
        for pass_projection in finishing_passes:
            for path_index, (path_start, path_end) in enumerate(finishing_paths):
                ordered_start, ordered_end = (path_start, path_end) if path_index % 2 == 0 else (path_end, path_start)
                cut_start = self._point_at_projection(ordered_start, pass_projection)
                cut_end = self._point_at_projection(ordered_end, pass_projection)
                finishing_motions.extend(
                    self._build_milling_path_motions(
                        [cut_start, cut_end],
                        top_projection=center_projection,
                        safe_projection=safe_projection,
                        parameters=finishing_parameters,
                    )
                )

        finishing_metadata = {
            "width": round(width, 4),
            "length": round(length, 4),
            "depth": round(depth, 4),
            "through": through,
            "pass_count": len(finishing_passes),
            "finish_path_count": len(finishing_paths),
        }
        finishing_metadata.update(self._build_feature_metadata(feature_context))
        finishing_metadata.update(self._build_parameter_metadata(finishing_parameters, finishing_request))
        operations.append(
            ToolpathOperation(
                op_id=f"slot_{index}_finish",
                strategy="slot_milling",
                feature_type="slot",
                motions=finishing_motions,
                metadata=finishing_metadata,
            )
        )

        return operations

    def _build_cavity_ops(
        self,
        features: Iterable[Any],
        safe_projection: float,
    ) -> tuple[list[ToolpathOperation], list[str]]:
        operations: list[ToolpathOperation] = []
        warnings: list[str] = []

        for idx, feature in enumerate(features):
            feature_type = getattr(feature, "type", "")
            if feature_type == "step":
                warnings.append(
                    f"step feature {idx} requires stock/setup-aware planning; skipping automatic cavity clearing"
                )
                continue
            if feature_type not in {"pocket", "opening"}:
                continue

            operations_for_feature = self._build_cavity_operations(feature, idx, safe_projection)
            if not operations_for_feature:
                warnings.append(f"{feature_type} feature {idx} is missing a usable planar region; skipping cavity clearing")
                continue
            operations.extend(operations_for_feature)

        return operations, warnings

    def _build_cavity_operations(
        self,
        feature: Any,
        index: int,
        safe_projection: float,
    ) -> list[ToolpathOperation]:
        region = self._resolve_planar_region(feature)
        if region is None:
            return []

        depth = float(feature.props.get("depth", 0.0) or 0.0)
        if depth <= 1e-4:
            return []
        geometry_source = "feature_boundary_loops" if region.boundary_loops else "feature_planar_region"
        feature_context = self._classify_feature_context(feature, geometry_source=geometry_source)

        through = bool(feature.props.get("through", False))
        local_top_projection = region.projection + depth
        target_projection = region.projection - (0.5 if through else 0.0)
        operations: list[ToolpathOperation] = []

        roughing_request = OperationRequest(
            strategy="cavity_clearing",
            feature_type=getattr(feature, "type", "cavity"),
            operation_mode="roughing",
            depth=local_top_projection - target_projection,
            span_u=region.span_u,
            span_v=region.span_v,
            through=through,
        )
        roughing_parameters = self._resolve_operation_parameters(roughing_request)
        if roughing_parameters is not None:
            roughing_passes = self._build_depth_pass_projections(
                local_top_projection,
                target_projection,
                max_stepdown=roughing_parameters.max_stepdown,
            )
            cut_paths, clearing_style = self._build_cavity_roughing_paths(
                region,
                roughing_parameters,
                allow_occ_offsets=False,
            )
            if cut_paths:
                roughing_motions: list[MotionCommand] = []
                for pass_projection in roughing_passes:
                    for path_index, cut_path in enumerate(cut_paths):
                        ordered_path = cut_path if path_index % 2 == 0 else list(reversed(cut_path))
                        projected_path = [self._point_at_projection(point, pass_projection) for point in ordered_path]
                        roughing_motions.extend(
                            self._build_milling_path_motions(
                                projected_path,
                                top_projection=local_top_projection,
                                safe_projection=safe_projection,
                                parameters=roughing_parameters,
                            )
                        )

                roughing_metadata = {
                    "depth": round(depth, 4),
                    "through": through,
                    "span_u": round(region.span_u, 4),
                    "span_v": round(region.span_v, 4),
                    "pass_count": len(roughing_passes),
                    "line_count": len(cut_paths),
                    "path_count": len(cut_paths),
                    "clearing_style": clearing_style,
                }
                roughing_metadata.update(self._build_feature_metadata(feature_context))
                roughing_metadata.update(self._build_parameter_metadata(roughing_parameters, roughing_request))
                operations.append(
                    ToolpathOperation(
                        op_id=f"cavity_{index}_rough",
                        strategy="cavity_clearing",
                        feature_type=getattr(feature, "type", "cavity"),
                        motions=roughing_motions,
                        metadata=roughing_metadata,
                    )
                )

        finishing_request = OperationRequest(
            strategy="cavity_clearing",
            feature_type=getattr(feature, "type", "cavity"),
            operation_mode="finishing",
            depth=local_top_projection - target_projection,
            span_u=region.span_u,
            span_v=region.span_v,
            through=through,
        )
        finishing_parameters = self._resolve_operation_parameters(finishing_request)
        if finishing_parameters is None:
            return operations

        boundary_paths, boundary_style = self._build_cavity_finishing_paths(
            region,
            finishing_parameters.tool.diameter,
            allow_occ_offsets=False,
        )
        if not boundary_paths:
            return operations

        finishing_passes = self._build_depth_pass_projections(
            local_top_projection,
            target_projection,
            max_stepdown=finishing_parameters.max_stepdown,
        )
        finishing_motions: list[MotionCommand] = []
        for pass_projection in finishing_passes:
            for path_index, boundary_path in enumerate(boundary_paths):
                ordered_path = boundary_path if path_index % 2 == 0 else list(reversed(boundary_path))
                pass_path = [self._point_at_projection(point, pass_projection) for point in ordered_path]
                finishing_motions.extend(
                    self._build_milling_path_motions(
                        pass_path,
                        top_projection=local_top_projection,
                        safe_projection=safe_projection,
                        parameters=finishing_parameters,
                    )
                )

        finishing_metadata = {
            "depth": round(depth, 4),
            "through": through,
            "span_u": round(region.span_u, 4),
            "span_v": round(region.span_v, 4),
            "pass_count": len(finishing_passes),
            "boundary_point_count": sum(len(path) for path in boundary_paths),
            "boundary_path_count": len(boundary_paths),
            "boundary_style": boundary_style,
        }
        finishing_metadata.update(self._build_feature_metadata(feature_context))
        finishing_metadata.update(self._build_parameter_metadata(finishing_parameters, finishing_request))
        operations.append(
            ToolpathOperation(
                op_id=f"cavity_{index}_finish",
                strategy="cavity_clearing",
                feature_type=getattr(feature, "type", "cavity"),
                motions=finishing_motions,
                metadata=finishing_metadata,
            )
        )

        return operations

    def _build_cavity_finishing_boundary(
        self,
        region: PlanarRegion,
        tool_diameter: float,
    ) -> list[np.ndarray]:
        boundary_paths, _boundary_style = self._build_cavity_finishing_paths(region, tool_diameter)
        return boundary_paths[0] if len(boundary_paths) == 1 else []

    def _build_cavity_finishing_paths(
        self,
        region: PlanarRegion,
        tool_diameter: float,
        *,
        allow_occ_offsets: bool = True,
    ) -> tuple[list[list[np.ndarray]], str]:
        tool_radius = float(tool_diameter) * 0.5
        if region.boundary_loops and tool_radius > 1e-9:
            outer_loop = region.boundary_loops[0]
            island_loops = region.boundary_loops[1:]
            boundary_paths = self._offset_loop_by_area_preference(
                outer_loop,
                tool_radius,
                prefer_smaller_area=True,
                allow_occ=allow_occ_offsets,
            )
            for island_loop in island_loops:
                boundary_paths.extend(
                    self._offset_loop_by_area_preference(
                        island_loop,
                        tool_radius,
                        prefer_smaller_area=False,
                        allow_occ=allow_occ_offsets,
                    )
                )
            if boundary_paths:
                return boundary_paths, "offset_boundary"

        half_u = region.span_u * 0.5 - float(tool_diameter) * 0.5
        half_v = region.span_v * 0.5 - float(tool_diameter) * 0.5
        if half_u <= 1e-9 or half_v <= 1e-9:
            return [], "bbox_rectangle"

        center_u = float(np.dot(region.center, region.basis_u))
        center_v = float(np.dot(region.center, region.basis_v))
        coordinates = [
            (-half_u, -half_v),
            (half_u, -half_v),
            (half_u, half_v),
            (-half_u, half_v),
            (-half_u, -half_v),
        ]
        return ([[
            self._point_from_plane_coordinates(
                center_u + u_coord,
                center_v + v_coord,
                region.projection,
                region.basis_u,
                region.basis_v,
            )
            for u_coord, v_coord in coordinates
        ]], "bbox_rectangle")

    def _get_in_plane_direction(self, axis: np.ndarray) -> Optional[np.ndarray]:
        projected = axis - self.working_plane_normal * float(np.dot(axis, self.working_plane_normal))
        norm = float(np.linalg.norm(projected))
        if norm <= 1e-9:
            return None
        return projected / norm

    def _get_slot_centerline_endpoints(
        self,
        center: np.ndarray,
        length_direction: np.ndarray,
        width: float,
        length: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        straight_span = max(0.0, length - width)
        half_span = straight_span / 2.0
        return (
            center - length_direction * half_span,
            center + length_direction * half_span,
        )

    def _get_slot_width_direction(self, length_direction: np.ndarray) -> np.ndarray:
        width_direction = np.cross(self.working_plane_normal, length_direction)
        norm = float(np.linalg.norm(width_direction))
        if norm <= 1e-9:
            basis_u, _basis_v = self._get_working_plane_basis()
            return basis_u
        return width_direction / norm

    def _build_slot_finishing_paths(
        self,
        start_point: np.ndarray,
        end_point: np.ndarray,
        width_direction: np.ndarray,
        slot_width: float,
        tool_diameter: float,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        offset_distance = max((float(slot_width) - float(tool_diameter)) * 0.5, 0.0)
        if offset_distance <= 1e-9:
            return [(np.array(start_point, dtype=float), np.array(end_point, dtype=float))]
        offset_vector = np.array(width_direction, dtype=float) * offset_distance
        return [
            (np.array(start_point, dtype=float) + offset_vector, np.array(end_point, dtype=float) + offset_vector),
            (np.array(start_point, dtype=float) - offset_vector, np.array(end_point, dtype=float) - offset_vector),
        ]

    def _build_slot_roughing_paths(
        self,
        start_point: np.ndarray,
        end_point: np.ndarray,
        length_direction: np.ndarray,
        width_direction: np.ndarray,
        slot_width: float,
        slot_length: float,
        slot_depth: float,
        tool_diameter: float,
        stepover_ratio: float,
    ) -> tuple[list[list[np.ndarray]], str]:
        offset_limit = max((float(slot_width) - float(tool_diameter)) * 0.5, 0.0)
        roughing_style = self._select_slot_roughing_style(
            slot_width=float(slot_width),
            slot_depth=float(slot_depth),
            tool_diameter=float(tool_diameter),
        )
        if offset_limit <= 1e-9:
            return [[np.array(start_point, dtype=float), np.array(end_point, dtype=float)]], "centerline"

        if roughing_style == "trochoidal":
            trochoidal_path = self._build_slot_trochoidal_path(
                start_point=np.array(start_point, dtype=float),
                end_point=np.array(end_point, dtype=float),
                length_direction=np.array(length_direction, dtype=float),
                width_direction=np.array(width_direction, dtype=float),
                slot_width=float(slot_width),
                slot_length=float(slot_length),
                tool_diameter=float(tool_diameter),
            )
            if len(trochoidal_path) >= 2:
                return [trochoidal_path], "trochoidal"

        stepover = max(float(tool_diameter) * float(stepover_ratio), 1e-6)
        offsets = [0.0]
        next_offset = stepover
        while next_offset < offset_limit - 1e-9:
            offsets.extend((next_offset, -next_offset))
            next_offset += stepover

        return ([
            [
                np.array(start_point, dtype=float) + np.array(width_direction, dtype=float) * offset,
                np.array(end_point, dtype=float) + np.array(width_direction, dtype=float) * offset,
            ]
            for offset in offsets
        ], "multi_lane")

    @staticmethod
    def _select_slot_roughing_style(
        *,
        slot_width: float,
        slot_depth: float,
        tool_diameter: float,
    ) -> str:
        if tool_diameter <= 1e-9 or slot_width <= tool_diameter + 1e-9:
            return "centerline"
        width_ratio = slot_width / tool_diameter
        if width_ratio <= 1.75 or slot_depth >= tool_diameter * 1.5:
            return "trochoidal"
        return "multi_lane"

    def _build_slot_trochoidal_path(
        self,
        *,
        start_point: np.ndarray,
        end_point: np.ndarray,
        length_direction: np.ndarray,
        width_direction: np.ndarray,
        slot_width: float,
        slot_length: float,
        tool_diameter: float,
    ) -> list[np.ndarray]:
        clearance = max((float(slot_width) - float(tool_diameter)) * 0.5, 0.0)
        if clearance <= 1e-9:
            return [np.array(start_point, dtype=float), np.array(end_point, dtype=float)]

        straight_length = max(float(slot_length) - float(slot_width), 0.0)
        if straight_length <= 1e-9:
            return [np.array(start_point, dtype=float), np.array(end_point, dtype=float)]

        amplitude = clearance * 0.9
        forward_step = max(float(tool_diameter) * 0.35, straight_length / 18.0, 1e-3)
        segment_count = max(8, int(math.ceil(straight_length / forward_step)))
        cycle_pitch = max(float(tool_diameter) * 0.85, straight_length / 4.0, 1e-3)
        cycle_count = max(1.0, straight_length / cycle_pitch)

        points: list[np.ndarray] = [np.array(start_point, dtype=float)]
        for segment_index in range(1, segment_count):
            progress = float(segment_index) / float(segment_count)
            along = np.array(start_point, dtype=float) + np.array(length_direction, dtype=float) * (straight_length * progress)
            phase = progress * cycle_count * 2.0 * math.pi
            lateral = np.array(width_direction, dtype=float) * (amplitude * math.sin(phase))
            points.append(along + lateral)
        points.append(np.array(end_point, dtype=float))
        return points

    def _resolve_planar_region(self, feature: Any) -> Optional[PlanarRegion]:
        props = getattr(feature, "props", {})
        boundary_loops = self._normalize_planar_loops(props.get("boundary_loops") or [])
        if boundary_loops:
            return self._build_planar_region_from_loops(boundary_loops)

        center = props.get("center")
        span = props.get("span")
        if center is not None and span is not None and len(span) == 2:
            center_np = np.array(center, dtype=float)
            basis_u, basis_v = self._get_working_plane_basis()
            return PlanarRegion(
                center=center_np,
                basis_u=basis_u,
                basis_v=basis_v,
                span_u=float(span[0]),
                span_v=float(span[1]),
                projection=float(np.dot(center_np, self.working_plane_normal)),
                boundary_loops=[],
            )

        geometry = getattr(feature, "geometry", None)
        if geometry is None:
            return None

        return self._extract_planar_region_from_geometry(geometry)

    def _build_planar_region_from_loops(
        self,
        boundary_loops: Iterable[Iterable[Any]],
    ) -> Optional[PlanarRegion]:
        normalized_loops = self._normalize_planar_loops(boundary_loops)
        if not normalized_loops:
            return None

        basis_u, basis_v = self._get_working_plane_basis()
        all_points = [point for loop in normalized_loops for point in loop[:-1]]
        if not all_points:
            return None

        u_vals = [float(np.dot(point, basis_u)) for point in all_points]
        v_vals = [float(np.dot(point, basis_v)) for point in all_points]
        projection = float(np.mean([np.dot(point, self.working_plane_normal) for point in all_points]))
        u_center = (min(u_vals) + max(u_vals)) / 2.0
        v_center = (min(v_vals) + max(v_vals)) / 2.0
        center = self._point_from_plane_coordinates(u_center, v_center, projection, basis_u, basis_v)

        return PlanarRegion(
            center=center,
            basis_u=basis_u,
            basis_v=basis_v,
            span_u=max(u_vals) - min(u_vals),
            span_v=max(v_vals) - min(v_vals),
            projection=projection,
            boundary_loops=normalized_loops,
        )

    def _normalize_planar_loops(
        self,
        loops: Iterable[Iterable[Any]],
    ) -> list[list[np.ndarray]]:
        normalized_loops: list[list[np.ndarray]] = []

        for loop in loops:
            points = [np.array(point, dtype=float) for point in loop]
            if len(points) < 3:
                continue
            if np.linalg.norm(points[0] - points[-1]) > 1e-6:
                points.append(np.array(points[0], dtype=float))
            normalized_loops.append(points)

        normalized_loops.sort(key=lambda loop: abs(self._get_loop_signed_area(loop)), reverse=True)
        return normalized_loops

    def _extract_planar_region_from_geometry(self, face: Any) -> Optional[PlanarRegion]:
        try:
            from OCP.BRepBndLib import BRepBndLib
            from OCP.Bnd import Bnd_Box
            from OCP.TopAbs import TopAbs_WIRE
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopoDS import TopoDS

            bbox = Bnd_Box()
            BRepBndLib.Add_s(face, bbox)
            xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
            corners = [
                np.array([xmin, ymin, zmin]),
                np.array([xmax, ymin, zmin]),
                np.array([xmin, ymax, zmin]),
                np.array([xmax, ymax, zmin]),
                np.array([xmin, ymin, zmax]),
                np.array([xmax, ymin, zmax]),
                np.array([xmin, ymax, zmax]),
                np.array([xmax, ymax, zmax]),
            ]
            wires: list[Any] = []
            exp_wires = TopExp_Explorer(face, TopAbs_WIRE)
            while exp_wires.More():
                wires.append(TopoDS.Wire_s(exp_wires.Current()))
                exp_wires.Next()

            if wires:
                planar_region = self._build_planar_region_from_loops(
                    self.wires_to_polylines(wires, samples_per_edge=6, max_deflection=0.02)
                )
                if planar_region is not None:
                    return planar_region

            basis_u, basis_v = self._get_working_plane_basis()
            u_vals = [float(np.dot(corner, basis_u)) for corner in corners]
            v_vals = [float(np.dot(corner, basis_v)) for corner in corners]
            projection = self._sample_face_projection(face)
            u_center = (min(u_vals) + max(u_vals)) / 2.0
            v_center = (min(v_vals) + max(v_vals)) / 2.0
            center = self._point_from_plane_coordinates(u_center, v_center, projection, basis_u, basis_v)
            return PlanarRegion(
                center=center,
                basis_u=basis_u,
                basis_v=basis_v,
                span_u=max(u_vals) - min(u_vals),
                span_v=max(v_vals) - min(v_vals),
                projection=projection,
                boundary_loops=[],
            )
        except Exception:
            return None

    def _sample_face_projection(self, face: Any) -> float:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.TopoDS import TopoDS

        surface = BRepAdaptor_Surface(TopoDS.Face_s(face), True)
        u_mid = (surface.FirstUParameter() + surface.LastUParameter()) / 2.0
        v_mid = (surface.FirstVParameter() + surface.LastVParameter()) / 2.0
        point = surface.Value(u_mid, v_mid)
        point_np = np.array([point.X(), point.Y(), point.Z()], dtype=float)
        return float(np.dot(point_np, self.working_plane_normal))

    def _build_cavity_cut_lines(
        self,
        region: PlanarRegion,
        parameters: MachiningParameters,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        if region.span_u <= 0.0 or region.span_v <= 0.0:
            return []

        if region.span_u >= region.span_v:
            major_basis, minor_basis = region.basis_u, region.basis_v
            major_span, minor_span = region.span_u, region.span_v
        else:
            major_basis, minor_basis = region.basis_v, region.basis_u
            major_span, minor_span = region.span_v, region.span_u

        tool_diameter = float(parameters.tool.diameter)
        major_half = max(major_span * 0.5 - tool_diameter * 0.5, 0.0)
        minor_half = max(minor_span * 0.5 - tool_diameter * 0.5, 0.0)
        offsets = self._build_stepover_offsets(minor_half, parameters)

        cut_lines: list[tuple[np.ndarray, np.ndarray]] = []
        for offset in offsets:
            line_center = region.center + minor_basis * offset
            cut_lines.append((
                line_center - major_basis * major_half,
                line_center + major_basis * major_half,
            ))

        return cut_lines

    def _build_cavity_roughing_paths(
        self,
        region: PlanarRegion,
        parameters: MachiningParameters,
        *,
        allow_occ_offsets: bool = True,
    ) -> tuple[list[list[np.ndarray]], str]:
        if region.boundary_loops:
            contour_paths = self._build_cavity_contour_parallel_paths(
                region.boundary_loops,
                parameters,
                allow_occ_offsets=allow_occ_offsets,
            )
            if contour_paths:
                return contour_paths, "contour_parallel"

        cut_lines = self._build_cavity_cut_lines(region, parameters)
        return [[line_start, line_end] for line_start, line_end in cut_lines], "raster_bbox"

    def _build_cavity_contour_parallel_paths(
        self,
        boundary_loops: list[list[np.ndarray]],
        parameters: MachiningParameters,
        *,
        allow_occ_offsets: bool = True,
    ) -> list[list[np.ndarray]]:
        if not boundary_loops:
            return []

        tool_radius = float(parameters.tool.diameter) * 0.5
        if tool_radius <= 1e-9:
            return []

        outer_loop = boundary_loops[0]
        island_loops = boundary_loops[1:]

        current_outer_loops = self._offset_loop_by_area_preference(
            outer_loop,
            tool_radius,
            prefer_smaller_area=True,
            allow_occ=allow_occ_offsets,
        )
        if not current_outer_loops:
            return []

        stepover = max(float(parameters.tool.diameter) * float(parameters.stepover_ratio), 1e-6)
        contour_paths: list[list[np.ndarray]] = []
        radial_distance = tool_radius

        for _ in range(256):
            level_paths = [list(loop) for loop in current_outer_loops]
            for island_loop in island_loops:
                level_paths.extend(
                    self._offset_loop_by_area_preference(
                        island_loop,
                        radial_distance,
                        prefer_smaller_area=False,
                        allow_occ=allow_occ_offsets,
                    )
                )

            if not level_paths:
                break

            contour_paths.extend(level_paths)
            next_outer_loops: list[list[np.ndarray]] = []
            for loop in current_outer_loops:
                next_outer_loops.extend(
                    self._offset_loop_by_area_preference(
                        loop,
                        stepover,
                        prefer_smaller_area=True,
                        allow_occ=allow_occ_offsets,
                    )
                )
            if not next_outer_loops:
                break

            current_outer_loops = next_outer_loops
            radial_distance += stepover

        return contour_paths

    def _offset_loop_by_area_preference(
        self,
        points: list[np.ndarray],
        offset_distance: float,
        *,
        prefer_smaller_area: bool,
        allow_occ: bool = True,
    ) -> list[list[np.ndarray]]:
        closed_loops = self._normalize_planar_loops([points])
        if not closed_loops:
            return []

        closed_points = closed_loops[0]
        distance = abs(float(offset_distance))
        if distance <= 1e-9:
            return [closed_points]

        original_area = abs(self._get_loop_signed_area(closed_points))
        best_candidates: list[list[np.ndarray]] = []
        best_area: Optional[float] = None

        for signed_distance in (distance, -distance):
            candidate_loops = (
                self._offset_planar_loops_occ(closed_points, signed_distance)
                if allow_occ
                else self._offset_planar_loops_polyline(closed_points, signed_distance)
            )
            filtered: list[tuple[float, list[np.ndarray]]] = []
            for candidate in candidate_loops:
                candidate_area = abs(self._get_loop_signed_area(candidate))
                if prefer_smaller_area and candidate_area < original_area - 1e-6:
                    filtered.append((candidate_area, candidate))
                if not prefer_smaller_area and candidate_area > original_area + 1e-6:
                    filtered.append((candidate_area, candidate))

            if not filtered:
                continue

            filtered.sort(key=lambda item: item[0], reverse=prefer_smaller_area)
            candidate_area = filtered[0][0]
            if best_area is None or (
                prefer_smaller_area and candidate_area > best_area
            ) or (
                not prefer_smaller_area and candidate_area < best_area
            ):
                best_area = candidate_area
                best_candidates = [loop for _area, loop in filtered]

        return best_candidates

    def _offset_planar_loops_occ(
        self,
        points: list[np.ndarray],
        offset_distance: float,
    ) -> list[list[np.ndarray]]:
        closed_loops = self._normalize_planar_loops([points])
        if not closed_loops:
            return []

        closed_points = closed_loops[0]
        if abs(float(offset_distance)) <= 1e-9:
            return [closed_points]

        try:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
            from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
            from OCP.GeomAbs import GeomAbs_Intersection
            from OCP.TopAbs import TopAbs_WIRE
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopoDS import TopoDS
            from OCP.gp import gp_Pnt
        except Exception:
            return self._offset_planar_loops_polyline(closed_points, offset_distance)

        polygon = BRepBuilderAPI_MakePolygon()
        for point in closed_points[:-1]:
            polygon.Add(gp_Pnt(float(point[0]), float(point[1]), float(point[2])))
        polygon.Close()

        try:
            wire = polygon.Wire()
            offset_builder = BRepOffsetAPI_MakeOffset()
            offset_builder.Init(GeomAbs_Intersection)
            offset_builder.AddWire(wire)
            offset_builder.Perform(float(offset_distance), 0.0)
            offset_shape = offset_builder.Shape()
        except Exception:
            return self._offset_planar_loops_polyline(closed_points, offset_distance)

        if offset_shape.IsNull():
            return self._offset_planar_loops_polyline(closed_points, offset_distance)

        wires: list[Any] = []
        exp_wires = TopExp_Explorer(offset_shape, TopAbs_WIRE)
        while exp_wires.More():
            wires.append(TopoDS.Wire_s(exp_wires.Current()))
            exp_wires.Next()
        if not wires:
            return self._offset_planar_loops_polyline(closed_points, offset_distance)

        normalized = self._normalize_planar_loops(
            self.wires_to_polylines(wires, samples_per_edge=8, max_deflection=0.01)
        )
        if normalized:
            return normalized
        return self._offset_planar_loops_polyline(closed_points, offset_distance)

    def _offset_planar_loops_polyline(
        self,
        points: list[np.ndarray],
        offset_distance: float,
    ) -> list[list[np.ndarray]]:
        closed_loops = self._normalize_planar_loops([points])
        if not closed_loops:
            return []

        closed_points = closed_loops[0]
        if abs(float(offset_distance)) <= 1e-9:
            return [closed_points]

        basis_u, basis_v = self._get_working_plane_basis()
        projection = float(np.mean([np.dot(point, self.working_plane_normal) for point in closed_points[:-1]]))
        projected_vertices = [
            np.array([
                float(np.dot(point, basis_u)),
                float(np.dot(point, basis_v)),
            ], dtype=float)
            for point in closed_points[:-1]
        ]
        projected_vertices = self._simplify_closed_loop_vertices_2d(projected_vertices)
        if len(projected_vertices) < 3:
            return []

        offset_vertices: list[np.ndarray] = []
        distance = float(offset_distance)
        for index, current in enumerate(projected_vertices):
            prev_point = projected_vertices[index - 1]
            next_point = projected_vertices[(index + 1) % len(projected_vertices)]

            prev_direction = current - prev_point
            next_direction = next_point - current
            prev_norm = float(np.linalg.norm(prev_direction))
            next_norm = float(np.linalg.norm(next_direction))
            if prev_norm <= 1e-9 or next_norm <= 1e-9:
                continue

            prev_direction /= prev_norm
            next_direction /= next_norm
            prev_normal = np.array([-prev_direction[1], prev_direction[0]], dtype=float)
            next_normal = np.array([-next_direction[1], next_direction[0]], dtype=float)

            offset_prev_point = current + prev_normal * distance
            offset_next_point = current + next_normal * distance
            intersection = self._intersect_lines_2d(
                offset_prev_point,
                prev_direction,
                offset_next_point,
                next_direction,
            )

            if intersection is None or float(np.linalg.norm(intersection - current)) > abs(distance) * 8.0:
                bisector = prev_normal + next_normal
                bisector_norm = float(np.linalg.norm(bisector))
                if bisector_norm <= 1e-9:
                    bisector = prev_normal
                    bisector_norm = float(np.linalg.norm(bisector))
                if bisector_norm <= 1e-9:
                    continue
                bisector /= bisector_norm
                denominator = max(abs(float(np.dot(bisector, prev_normal))), 0.25)
                intersection = current + bisector * (distance / denominator)

            if not np.isfinite(intersection).all():
                return []
            offset_vertices.append(intersection)

        offset_vertices = self._simplify_closed_loop_vertices_2d(offset_vertices)
        if len(offset_vertices) < 3:
            return []

        offset_loop = [
            self._point_from_plane_coordinates(
                float(vertex[0]),
                float(vertex[1]),
                projection,
                basis_u,
                basis_v,
            )
            for vertex in offset_vertices
        ]
        offset_loop.append(np.array(offset_loop[0], dtype=float))
        normalized = self._normalize_planar_loops([offset_loop])
        if not normalized:
            return []
        area = abs(self._get_loop_signed_area(normalized[0]))
        if area <= 1e-6:
            return []
        return normalized

    @staticmethod
    def _simplify_closed_loop_vertices_2d(
        vertices: Iterable[np.ndarray],
        *,
        distance_tolerance: float = 1e-6,
        collinear_tolerance: float = 1e-6,
    ) -> list[np.ndarray]:
        filtered: list[np.ndarray] = []
        for vertex in vertices:
            vertex_np = np.array(vertex, dtype=float)
            if filtered and float(np.linalg.norm(vertex_np - filtered[-1])) <= distance_tolerance:
                continue
            filtered.append(vertex_np)

        if len(filtered) > 1 and float(np.linalg.norm(filtered[0] - filtered[-1])) <= distance_tolerance:
            filtered.pop()

        changed = True
        while changed and len(filtered) >= 3:
            changed = False
            simplified: list[np.ndarray] = []
            for index, current in enumerate(filtered):
                prev_point = filtered[index - 1]
                next_point = filtered[(index + 1) % len(filtered)]
                prev_vector = current - prev_point
                next_vector = next_point - current
                prev_norm = float(np.linalg.norm(prev_vector))
                next_norm = float(np.linalg.norm(next_vector))
                if prev_norm <= distance_tolerance or next_norm <= distance_tolerance:
                    changed = True
                    continue

                cross = abs(float(prev_vector[0] * next_vector[1] - prev_vector[1] * next_vector[0]))
                if cross <= collinear_tolerance * prev_norm * next_norm and float(np.dot(prev_vector, next_vector)) >= 0.0:
                    changed = True
                    continue
                simplified.append(current)
            filtered = simplified

        return filtered

    @staticmethod
    def _intersect_lines_2d(
        point_a: np.ndarray,
        direction_a: np.ndarray,
        point_b: np.ndarray,
        direction_b: np.ndarray,
    ) -> Optional[np.ndarray]:
        denominator = float(direction_a[0] * direction_b[1] - direction_a[1] * direction_b[0])
        if abs(denominator) <= 1e-9:
            return None

        delta = np.array(point_b, dtype=float) - np.array(point_a, dtype=float)
        t_value = float((delta[0] * direction_b[1] - delta[1] * direction_b[0]) / denominator)
        return np.array(point_a, dtype=float) + np.array(direction_a, dtype=float) * t_value

    def _build_stepover_offsets(self, half_span: float, parameters: MachiningParameters) -> list[float]:
        if half_span <= 1e-9:
            return [0.0]

        stepover = max(float(parameters.tool.diameter) * parameters.stepover_ratio, 1e-6)
        line_count = max(1, int(np.floor((2.0 * half_span) / stepover)) + 1)
        if line_count == 1:
            return [0.0]
        return [float(value) for value in np.linspace(-half_span, half_span, line_count)]

    def _build_depth_pass_projections(
        self,
        start_projection: float,
        target_projection: float,
        max_stepdown: Optional[float] = None,
    ) -> list[float]:
        total_depth = float(start_projection - target_projection)
        stepdown = self.max_stepdown if max_stepdown is None else float(max_stepdown)
        if total_depth <= 0.0 or stepdown <= 0.0:
            return [float(target_projection)]

        pass_count = max(1, int(np.ceil(total_depth / stepdown)))
        return [
            float(start_projection - min((idx + 1) * stepdown, total_depth))
            for idx in range(pass_count)
        ]

    def _get_working_plane_basis(self) -> tuple[np.ndarray, np.ndarray]:
        ref = np.array([1.0, 0.0, 0.0], dtype=float)
        if abs(float(np.dot(ref, self.working_plane_normal))) > 0.9:
            ref = np.array([0.0, 1.0, 0.0], dtype=float)
        basis_u = ref - self.working_plane_normal * float(np.dot(ref, self.working_plane_normal))
        basis_u /= np.linalg.norm(basis_u)
        basis_v = np.cross(self.working_plane_normal, basis_u)
        basis_v /= np.linalg.norm(basis_v)
        return basis_u, basis_v

    def _point_from_plane_coordinates(
        self,
        u_coord: float,
        v_coord: float,
        projection: float,
        basis_u: np.ndarray,
        basis_v: np.ndarray,
    ) -> np.ndarray:
        return basis_u * float(u_coord) + basis_v * float(v_coord) + self.working_plane_normal * float(projection)

    def _get_planar_point_spans(self, points: Iterable[np.ndarray]) -> tuple[float, float]:
        basis_u, basis_v = self._get_working_plane_basis()
        point_list = [np.array(point, dtype=float) for point in points]
        u_vals = [float(np.dot(point, basis_u)) for point in point_list]
        v_vals = [float(np.dot(point, basis_v)) for point in point_list]
        return max(u_vals) - min(u_vals), max(v_vals) - min(v_vals)

    def _build_profile_ops(
        self,
        perimeter_loops: Iterable[Iterable[tuple[float, float, float]]],
        safe_projection: float,
        top_projection: float,
        *,
        geometry_source: str,
    ) -> list[ToolpathOperation]:
        operations: list[ToolpathOperation] = []
        normalized_loops = self._normalize_planar_loops(perimeter_loops)
        if self.profile_rough_only and normalized_loops:
            normalized_loops = normalized_loops[:1]

        for idx, loop in enumerate(normalized_loops):
            points = [np.array(point, dtype=float) for point in loop]
            if len(points) < 3:
                continue

            prefer_smaller_area = idx != 0

            if self.profile_stock_allowance > 1e-9:
                roughing_operation = self._build_profile_operation(
                    op_id=f"profile_{idx}_rough",
                    points=points,
                    safe_projection=safe_projection,
                    top_projection=top_projection,
                    operation_mode="roughing",
                    geometry_source=geometry_source,
                    stock_to_leave=self.profile_stock_allowance,
                    prefer_smaller_area=prefer_smaller_area,
                )
                if roughing_operation is not None:
                    operations.append(roughing_operation)

            if not self.profile_rough_only:
                finishing_operation = self._build_profile_operation(
                    op_id=f"profile_{idx}_finish" if self.profile_stock_allowance > 1e-9 else f"profile_{idx}",
                    points=points,
                    safe_projection=safe_projection,
                    top_projection=top_projection,
                    operation_mode="finishing",
                    geometry_source=geometry_source,
                    prefer_smaller_area=prefer_smaller_area,
                )
                if finishing_operation is not None:
                    operations.append(finishing_operation)

        return operations

    def _build_profile_operation(
        self,
        op_id: str,
        points: list[np.ndarray],
        safe_projection: float,
        top_projection: float,
        operation_mode: str,
        geometry_source: str,
        stock_to_leave: float = 0.0,
        prefer_smaller_area: bool = False,
        max_passes: Optional[int] = None,
    ) -> Optional[ToolpathOperation]:
        if len(points) < 3:
            return None

        boundary_points = [np.array(point, dtype=float) for point in points]
        if np.linalg.norm(boundary_points[0] - boundary_points[-1]) > 1e-6:
            boundary_points.append(boundary_points[0])

        start = boundary_points[0]
        start_projection = float(np.dot(start, self.working_plane_normal))
        span_u, span_v = self._get_planar_point_spans(boundary_points)
        request = OperationRequest(
            strategy="2p5d_profile",
            feature_type="perimeter",
            operation_mode=operation_mode,
            depth=max(top_projection - start_projection, 0.0),
            span_u=span_u,
            span_v=span_v,
        )
        parameters = self._resolve_operation_parameters(request)
        if parameters is None:
            return None

        radial_compensation = float(parameters.tool.diameter) * 0.5 + max(float(stock_to_leave), 0.0)
        tool_center_loops = self._build_profile_tool_center_loops(
            boundary_points,
            radial_compensation,
            prefer_smaller_area=prefer_smaller_area,
        )
        if not tool_center_loops:
            return None

        pass_projections = self._build_depth_pass_projections(
            top_projection,
            start_projection,
            max_stepdown=parameters.max_stepdown,
        )
        if max_passes is not None:
            pass_projections = pass_projections[:max_passes]
        motions: list[MotionCommand] = []
        for pass_projection in pass_projections:
            for path_index, tool_center_loop in enumerate(tool_center_loops):
                ordered_loop = tool_center_loop if path_index % 2 == 0 else list(reversed(tool_center_loop))
                pass_points = [self._point_at_projection(point, pass_projection) for point in ordered_loop]
                motions.extend(
                    self._build_milling_path_motions(
                        pass_points,
                        top_projection=top_projection,
                        safe_projection=safe_projection,
                        parameters=parameters,
                    )
                )

        metadata = {
            "point_count": sum(len(loop) for loop in tool_center_loops),
            "boundary_point_count": len(boundary_points),
            "path_count": len(tool_center_loops),
            "pass_count": len(pass_projections),
            "span_u": round(span_u, 4),
            "span_v": round(span_v, 4),
            "tool_radius_compensation": round(radial_compensation, 4),
            "cutter_side": "inside" if prefer_smaller_area else "outside",
        }
        metadata.update(
            self._build_feature_metadata(
                self._build_perimeter_context(closed=True, geometry_source=geometry_source)
            )
        )
        if stock_to_leave > 1e-9:
            metadata["radial_stock_to_leave"] = round(stock_to_leave, 4)
        metadata.update(self._build_parameter_metadata(parameters, request))

        return ToolpathOperation(
            op_id=op_id,
            strategy="2p5d_profile",
            feature_type="perimeter",
            motions=motions,
            metadata=metadata,
        )

    def _build_profile_tool_center_loops(
        self,
        points: list[np.ndarray],
        radial_compensation: float,
        *,
        prefer_smaller_area: bool,
    ) -> list[list[np.ndarray]]:
        closed_loops = self._normalize_planar_loops([points])
        if not closed_loops:
            return []

        closed_points = closed_loops[0]
        distance = abs(float(radial_compensation))
        if distance <= 1e-9:
            return [closed_points]

        return self._offset_loop_by_area_preference(
            closed_points,
            distance,
            prefer_smaller_area=prefer_smaller_area,
        )

    def _build_profile_stock_loops(
        self,
        points: list[np.ndarray],
        stock_allowance: float,
    ) -> list[list[np.ndarray]]:
        if stock_allowance <= 1e-9 or len(points) < 3:
            return [[np.array(point, dtype=float) for point in points]]

        closed = np.linalg.norm(points[0] - points[-1]) <= 1e-6
        unique_points = points[:-1] if closed else points
        if len(unique_points) < 3:
            return []

        occ_loops = self._build_profile_stock_loops_occ(unique_points, stock_allowance)
        if occ_loops:
            return occ_loops

        fallback_loop = self._build_profile_stock_loop_fallback(unique_points, stock_allowance)
        return [fallback_loop] if fallback_loop else []

    def _build_profile_stock_loops_occ(
        self,
        points: list[np.ndarray],
        stock_allowance: float,
    ) -> list[list[np.ndarray]]:
        return self._offset_loop_by_area_preference(
            points,
            stock_allowance,
            prefer_smaller_area=True,
        )

    def _build_profile_stock_loop_fallback(
        self,
        points: list[np.ndarray],
        stock_allowance: float,
    ) -> list[np.ndarray]:
        if len(points) < 3:
            return []

        basis_u, basis_v = self._get_working_plane_basis()
        projection = float(np.dot(points[0], self.working_plane_normal))
        u_vals = [float(np.dot(point, basis_u)) for point in points]
        v_vals = [float(np.dot(point, basis_v)) for point in points]
        center_u = (min(u_vals) + max(u_vals)) / 2.0
        center_v = (min(v_vals) + max(v_vals)) / 2.0

        offset_coordinates: list[tuple[float, float]] = []
        for u_coord, v_coord in zip(u_vals, v_vals):
            du = u_coord - center_u
            dv = v_coord - center_v
            offset_u = center_u if abs(du) <= 1e-9 else center_u + float(np.sign(du)) * max(abs(du) - stock_allowance, 0.0)
            offset_v = center_v if abs(dv) <= 1e-9 else center_v + float(np.sign(dv)) * max(abs(dv) - stock_allowance, 0.0)
            offset_coordinates.append((offset_u, offset_v))

        span_u = max(coord[0] for coord in offset_coordinates) - min(coord[0] for coord in offset_coordinates)
        span_v = max(coord[1] for coord in offset_coordinates) - min(coord[1] for coord in offset_coordinates)
        if span_u <= 1e-6 or span_v <= 1e-6:
            return []

        offset_points = [
            self._point_from_plane_coordinates(u_coord, v_coord, projection, basis_u, basis_v)
            for u_coord, v_coord in offset_coordinates
        ]
        offset_points.append(np.array(offset_points[0], dtype=float))
        return offset_points

    def _get_loop_signed_area(self, points: Iterable[np.ndarray]) -> float:
        point_list = [np.array(point, dtype=float) for point in points]
        if len(point_list) < 3:
            return 0.0
        if np.linalg.norm(point_list[0] - point_list[-1]) <= 1e-6:
            point_list = point_list[:-1]
        basis_u, basis_v = self._get_working_plane_basis()
        u_vals = [float(np.dot(point, basis_u)) for point in point_list]
        v_vals = [float(np.dot(point, basis_v)) for point in point_list]
        signed_area = 0.0
        for idx, (u_coord, v_coord) in enumerate(zip(u_vals, v_vals)):
            next_u, next_v = u_vals[(idx + 1) % len(u_vals)], v_vals[(idx + 1) % len(v_vals)]
            signed_area += u_coord * next_v - next_u * v_coord
        return signed_area * 0.5

    def _infer_top_projection(self, features: Iterable[Any]) -> float:
        projections: list[float] = []

        for feature in features:
            if getattr(feature, "type", "") == "hole_group":
                holes = getattr(feature, "holes", [])
                for hole in holes:
                    center = np.array(hole.props.get("center", (0.0, 0.0, 0.0)), dtype=float)
                    projections.append(float(np.dot(center, self.working_plane_normal)))
                continue

            if getattr(feature, "type", "") in {"pocket", "opening", "step"}:
                region = self._resolve_planar_region(feature)
                depth = float(getattr(feature, "props", {}).get("depth", 0.0) or 0.0)
                if region is not None:
                    projections.append(region.projection + depth)
                    continue

            center = feature.props.get("center") if hasattr(feature, "props") else None
            if center is None:
                continue
            center_np = np.array(center, dtype=float)
            projections.append(float(np.dot(center_np, self.working_plane_normal)))

        return max(projections) if projections else 0.0

    def _infer_top_projection_from_loops(
        self,
        loops: Iterable[Iterable[tuple[float, float, float]]],
    ) -> float:
        _, top_projection = self._infer_projection_bounds_from_loops(loops)
        return top_projection

    def _infer_projection_bounds_from_loops(
        self,
        loops: Iterable[Iterable[tuple[float, float, float]]],
    ) -> tuple[float, float]:
        projections: list[float] = []
        for loop in loops:
            for point in loop:
                point_np = np.array(point, dtype=float)
                if point_np.shape != (3,):
                    continue
                projections.append(float(np.dot(point_np, self.working_plane_normal)))
        if not projections:
            return 0.0, 0.0
        return min(projections), max(projections)

    def _point_at_projection(self, point: np.ndarray, projection: float) -> np.ndarray:
        current_projection = float(np.dot(point, self.working_plane_normal))
        delta = projection - current_projection
        return point + self.working_plane_normal * delta

    @staticmethod
    def _to_point(point: np.ndarray) -> Point3:
        return (float(point[0]), float(point[1]), float(point[2]))

    @staticmethod
    def _occ_point_to_tuple(point: Any) -> tuple[float, float, float]:
        return (float(point.X()), float(point.Y()), float(point.Z()))

    @staticmethod
    def _point_to_segment_distance(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> float:
        segment = np.array(end, dtype=float) - np.array(start, dtype=float)
        length_sq = float(np.dot(segment, segment))
        if length_sq <= 1e-18:
            return float(np.linalg.norm(np.array(point, dtype=float) - np.array(start, dtype=float)))
        t_value = float(np.dot(np.array(point, dtype=float) - np.array(start, dtype=float), segment) / length_sq)
        t_value = min(max(t_value, 0.0), 1.0)
        closest = np.array(start, dtype=float) + segment * t_value
        return float(np.linalg.norm(np.array(point, dtype=float) - closest))

    @classmethod
    def _sample_occ_curve_segment(
        cls,
        curve: Any,
        t_first: float,
        t_last: float,
        *,
        max_deflection: Optional[float],
        remaining_depth: int,
    ) -> list[tuple[float, float, float]]:
        first_point = cls._occ_point_to_tuple(curve.Value(float(t_first)))
        last_point = cls._occ_point_to_tuple(curve.Value(float(t_last)))
        if max_deflection is None or remaining_depth <= 0:
            return [first_point, last_point]

        t_mid = (float(t_first) + float(t_last)) * 0.5
        if abs(t_mid - float(t_first)) <= 1e-12 or abs(float(t_last) - t_mid) <= 1e-12:
            return [first_point, last_point]

        mid_point = cls._occ_point_to_tuple(curve.Value(t_mid))
        deviation = cls._point_to_segment_distance(
            np.array(mid_point, dtype=float),
            np.array(first_point, dtype=float),
            np.array(last_point, dtype=float),
        )
        if deviation <= float(max_deflection):
            return [first_point, last_point]

        left_points = cls._sample_occ_curve_segment(
            curve,
            float(t_first),
            t_mid,
            max_deflection=max_deflection,
            remaining_depth=remaining_depth - 1,
        )
        right_points = cls._sample_occ_curve_segment(
            curve,
            t_mid,
            float(t_last),
            max_deflection=max_deflection,
            remaining_depth=remaining_depth - 1,
        )
        return left_points[:-1] + right_points

    @classmethod
    def _sample_occ_curve_points(
        cls,
        curve: Any,
        t_first: float,
        t_last: float,
        *,
        samples_per_edge: int,
        max_deflection: Optional[float],
        max_depth: int = 10,
    ) -> list[tuple[float, float, float]]:
        if abs(float(t_last) - float(t_first)) <= 1e-12:
            return [cls._occ_point_to_tuple(curve.Value(float(t_first)))]

        edge_samples = max(2, int(samples_per_edge))
        parameter_values = [
            float(t_first) + (float(t_last) - float(t_first)) * sample_idx / float(edge_samples - 1)
            for sample_idx in range(edge_samples)
        ]

        sampled_points: list[tuple[float, float, float]] = []
        for sample_idx in range(len(parameter_values) - 1):
            segment_points = cls._sample_occ_curve_segment(
                curve,
                parameter_values[sample_idx],
                parameter_values[sample_idx + 1],
                max_deflection=max_deflection,
                remaining_depth=max_depth,
            )
            if not segment_points:
                continue
            if sampled_points and np.linalg.norm(np.array(sampled_points[-1]) - np.array(segment_points[0])) <= 1e-7:
                sampled_points.extend(segment_points[1:])
            else:
                sampled_points.extend(segment_points)
        return sampled_points

    @classmethod
    def wires_to_polylines(
        cls,
        wires: Iterable[Any],
        samples_per_edge: int = 8,
        max_deflection: Optional[float] = 0.05,
    ) -> list[list[tuple[float, float, float]]]:
        """Sample OCC perimeter wires into polyline loops usable by the planner."""
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.BRepTools import BRepTools_WireExplorer
        from OCP.TopAbs import TopAbs_REVERSED
        from OCP.TopoDS import TopoDS

        polylines: list[list[tuple[float, float, float]]] = []
        edge_samples = max(2, int(samples_per_edge))

        for wire in wires:
            points: list[tuple[float, float, float]] = []
            exp_edges = BRepTools_WireExplorer(TopoDS.Wire_s(wire))
            while exp_edges.More():
                edge = TopoDS.Edge_s(exp_edges.Current())
                curve = BRepAdaptor_Curve(edge)
                t_first = curve.FirstParameter()
                t_last = curve.LastParameter()
                sampled_points = list(
                    cls._sample_occ_curve_points(
                    curve,
                    t_first,
                    t_last,
                    samples_per_edge=edge_samples,
                    max_deflection=max_deflection,
                    )
                )
                if edge.Orientation() == TopAbs_REVERSED:
                    sampled_points.reverse()
                for sampled in sampled_points:
                    if not points or np.linalg.norm(np.array(points[-1]) - np.array(sampled)) > 1e-7:
                        points.append(sampled)
                exp_edges.Next()
            if len(points) >= 3:
                if np.linalg.norm(np.array(points[0]) - np.array(points[-1])) > 1e-6:
                    points.append(points[0])
                polylines.append(points)

        return polylines

