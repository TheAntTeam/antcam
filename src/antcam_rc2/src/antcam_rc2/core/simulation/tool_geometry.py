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

import numpy as np

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


def _revolve_profile(profile: list[tuple[float, float]], segments: int) -> tuple[np.ndarray, np.ndarray]:
    """Revolve a ``(z, radius)`` profile around Z into a triangle mesh."""
    rings: list[tuple[float, float]] = [(float(z), float(r)) for z, r in profile]
    vertices: list[tuple[float, float, float]] = []
    for z, radius in rings:
        for index in range(segments):
            angle = 2.0 * math.pi * index / segments
            vertices.append((radius * math.cos(angle), radius * math.sin(angle), z))
    triangles: list[tuple[int, int, int]] = []
    for ring in range(len(rings) - 1):
        base0, base1 = ring * segments, (ring + 1) * segments
        for index in range(segments):
            nxt = (index + 1) % segments
            triangles.append((base0 + index, base1 + index, base1 + nxt))
            triangles.append((base0 + index, base1 + nxt, base0 + nxt))
    if rings[0][1] > 1e-9:  # bottom cap (flat tools)
        center = len(vertices)
        vertices.append((0.0, 0.0, rings[0][0]))
        for index in range(segments):
            triangles.append((center, index, (index + 1) % segments))
    if rings[-1][1] > 1e-9:  # top cap
        center = len(vertices)
        vertices.append((0.0, 0.0, rings[-1][0]))
        base = (len(rings) - 1) * segments
        for index in range(segments):
            nxt = (index + 1) % segments
            triangles.append((center, base + nxt, base + index))
    return np.asarray(vertices, dtype=np.float64), np.asarray(triangles, dtype=np.int64)


def _cutter_profile(cutter: AssemblyBody, steps: int) -> list[tuple[float, float]]:
    """Sample a cutter body into ``(z, radius)`` points, resolving its shape.

    The sampling is shape-aware: a ball nose gets enough points across the
    small hemispherical tip (a coarse linear sampling turned it into a cone),
    a V-bit is a straight cone and a cylinder needs only its two ends.
    """
    flute = cutter.z_offset_max
    if cutter.shape is BodyShape.BALL_CAP:
        points = [(cutter.radius * index / steps, 0.0) for index in range(steps + 1)]
        return [(z, max(0.0, cutter.radius_at(z))) for z, _ in points] + [(flute, cutter.radius_at(flute))]
    if cutter.shape is BodyShape.CONE:
        return [(0.0, 0.0), (flute, cutter.radius_at(flute))]
    return [(0.0, cutter.radius), (flute, cutter.radius)]


def assembly_marker_mesh(assembly: ToolAssembly, segments: int = 28, steps: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """A revolved mesh of the tool (cutter + shank), tip at the origin, +Z up.

    The profile follows the actual tool shape: cylinder (end mill), cone
    (V-bit), ball cap (ball nose) and the shank above the flutes.
    """
    cutter = assembly.cutter_body()
    shank = assembly.shank_body()
    flute = cutter.z_offset_max
    profile = _cutter_profile(cutter, steps)
    if shank.z_offset_max > flute:
        profile.append((flute, shank.radius))
        profile.append((shank.z_offset_max, shank.radius))
    return _revolve_profile(profile, segments)


__all__ = ["AssemblyBody", "BodyShape", "ToolAssembly", "assembly_marker_mesh"]
