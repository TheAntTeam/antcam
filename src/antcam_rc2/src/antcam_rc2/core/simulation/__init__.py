"""Voxel simulation and collision detection engine (UI-agnostic).

Consumes :class:`ToolpathPlan` artifacts (Phase 4) plus an immutable
``Project`` to produce deterministic :class:`SimulationReport` artifacts with
material removal, collision events and a sampled timeline.
"""

from __future__ import annotations

from antcam_rc2.core.simulation.collision import (
    CollisionHit,
    check_collisions,
    check_fixture,
    check_reach,
    check_stock_material,
    check_tip_in_work_area,
)
from antcam_rc2.core.simulation.models import (
    SimulationCode,
    SimulationEvent,
    SimulationReport,
    SimulationSettings,
    SimulationSeverity,
    SimulationStats,
    SimulationTick,
)
from antcam_rc2.core.simulation.simulator import SimulationResult, Simulator, VoxelCheckpoint
from antcam_rc2.core.simulation.sweep import remove_motion, remove_segment
from antcam_rc2.core.simulation.tool_geometry import AssemblyBody, BodyShape, ToolAssembly
from antcam_rc2.core.simulation.voxels import VoxelGrid

__all__ = [
    "AssemblyBody",
    "BodyShape",
    "CollisionHit",
    "SimulationCode",
    "SimulationEvent",
    "SimulationReport",
    "SimulationResult",
    "SimulationSettings",
    "SimulationSeverity",
    "SimulationStats",
    "SimulationTick",
    "Simulator",
    "ToolAssembly",
    "VoxelCheckpoint",
    "VoxelGrid",
    "check_collisions",
    "check_fixture",
    "check_reach",
    "check_stock_material",
    "check_tip_in_work_area",
    "remove_motion",
    "remove_segment",
]
