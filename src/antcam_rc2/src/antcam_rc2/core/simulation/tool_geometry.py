"""Tool assembly geometry: the vertical stack of cutter, shank, collet, spindle.

The assembly is a stack of axial bodies whose radius can vary with height
(cylinders, V-bit cones, ball-nose caps, T-slot neck/head).  All offsets are
relative to the tool tip; the simulator adds the current tip Z to obtain
absolute WCS heights.  When the catalog lacks the geometry required by a shape
(V-bit angle, T-slot head), the body falls back to a conservative cylinder and
``unknown_geometry_warning`` reports it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from antcam_rc2.core.databases.models import MachineProfile, Tool, ToolType

_COLLET_HOLDER_LENGTH_MM = 8.0


class BodyShape(StrEnum):
    """Cross-section shape of one axial assembly body."""

    CYLINDER = "cylinder"
    CONE = "cone"
    BALL_CAP = "ball_cap"


@dataclass(frozen=True, slots=True)
class AssemblyBody:
    """One axial body; ``radius_at`` is evaluated at a tip-relative offset."""

    name: str
    z_offset_min: float
    z_offset_max: float
    radius: float
    shape: BodyShape = BodyShape.CYLINDER
    is_cutting: bool = False

    def radius_at(self, z_offset: float) -> float:
        """Cross-section radius at a tip-relative height (clamped to the span)."""
        z = max(self.z_offset_min, min(self.z_offset_max, z_offset))
        if self.shape is BodyShape.CYLINDER:
            return self.radius
        if self.shape is BodyShape.CONE:
            span = self.z_offset_max - self.z_offset_min
            return self.radius * (0.0 if span <= 1e-12 else (z - self.z_offset_min) / span)
        if self.shape is BodyShape.BALL_CAP:
            local = z - self.z_offset_min
            if local >= self.radius:
                return self.radius  # the cylinder above the spherical cap
            inside = self.radius**2 - (local - self.radius) ** 2
            return math.sqrt(max(0.0, inside))
        return self.radius


@dataclass(frozen=True, slots=True)
class ToolAssembly:
    """The full vertical tool assembly derived from a tool and machine."""

    tool: Tool
    machine: MachineProfile
    collet_holder_length_mm: float = _COLLET_HOLDER_LENGTH_MM

    def cutter_body(self) -> AssemblyBody:
        """The cutting body (flute region) with the per-tool shape."""
        tool = self.tool
        flute = tool.flute_length_mm
        radius = tool.cutting_diameter_mm / 2.0
        if tool.tool_type is ToolType.V_BIT:
            if tool.v_bit_angle_deg is not None:
                return AssemblyBody(
                    name="cutter",
                    z_offset_min=0.0,
                    z_offset_max=flute,
                    radius=radius,
                    shape=BodyShape.CONE,
                    is_cutting=True,
                )
            return AssemblyBody(name="cutter", z_offset_min=0.0, z_offset_max=flute, radius=radius, is_cutting=True)
        if tool.tool_type is ToolType.BALL:
            return AssemblyBody(
                name="cutter",
                z_offset_min=0.0,
                z_offset_max=flute,
                radius=radius,
                shape=BodyShape.BALL_CAP,
                is_cutting=True,
            )
        if tool.tool_type is ToolType.T_SLOT:
            if tool.t_slot_head_diameter_mm is not None:
                radius = tool.t_slot_head_diameter_mm / 2.0
        return AssemblyBody(name="cutter", z_offset_min=0.0, z_offset_max=flute, radius=radius, is_cutting=True)

    def shank_body(self) -> AssemblyBody:
        """The shank region (non-cutting), narrower than the cutter head for T-slots."""
        tool = self.tool
        radius = tool.shank_diameter_mm / 2.0
        if tool.tool_type is ToolType.T_SLOT and tool.t_slot_neck_diameter_mm is not None:
            radius = tool.t_slot_neck_diameter_mm / 2.0
        return AssemblyBody(
            name="shank",
            z_offset_min=tool.flute_length_mm,
            z_offset_max=tool.overall_length_mm,
            radius=radius,
        )

    def non_cutting_bodies(self) -> tuple[AssemblyBody, ...]:
        """Shank, collet holder and spindle (checked for collisions)."""
        tool = self.tool
        machine = self.machine
        spindle_radius = machine.spindle_diameter_mm / 2.0
        holder_start = tool.overall_length_mm
        return (
            self.shank_body(),
            AssemblyBody(
                name="collet_holder",
                z_offset_min=holder_start,
                z_offset_max=holder_start + self.collet_holder_length_mm,
                radius=spindle_radius,
            ),
            AssemblyBody(
                name="spindle",
                z_offset_min=holder_start + self.collet_holder_length_mm,
                z_offset_max=holder_start + self.collet_holder_length_mm + machine.spindle_length_mm,
                radius=spindle_radius,
            ),
        )

    def unknown_geometry_warning(self) -> str | None:
        """A warning when a tool shape needs catalog geometry we lack."""
        tool = self.tool
        if tool.tool_type is ToolType.V_BIT and tool.v_bit_angle_deg is None:
            return f"tool '{tool.id}' has no v_bit_angle_deg; using a conservative cylinder"
        if tool.tool_type is ToolType.T_SLOT and tool.t_slot_head_diameter_mm is None:
            return f"tool '{tool.id}' has no t_slot_head_diameter_mm; using cutting_diameter as head"
        return None

    def max_reach_mm(self) -> float:
        """The maximum cutting depth reachable before the shank enters material."""
        return self.tool.flute_length_mm


__all__ = ["AssemblyBody", "BodyShape", "ToolAssembly"]
