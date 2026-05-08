"""Automatic 2.5D toolpath planning primitives.

This module introduces a first CAM planning layer that converts recognized
features and optional contour perimeters into neutral motion commands.

The generated plan is intentionally post-processor agnostic and can be
serialized to JSON or converted later to machine-specific G-code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional

import numpy as np

Point3 = tuple[float, float, float]


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


class AutoToolpathPlanner:
    """Build a first 2.5D toolpath plan from features and optional perimeter loops."""

    def __init__(
        self,
        working_plane_normal: tuple[float, float, float] = (0.0, 0.0, 1.0),
        safe_z_offset: float = 5.0,
        clearance_z: float = 1.0,
        plunge_feed: float = 120.0,
        cut_feed: float = 300.0,
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

    def generate(
        self,
        features: Iterable[Any],
        perimeter_wires: Optional[Iterable[Any]] = None,
        perimeter_polylines: Optional[Iterable[Iterable[tuple[float, float, float]]]] = None,
    ) -> ToolpathPlan:
        """Generate a neutral toolpath plan from extracted semantic data.

        Args:
            features: Extracted features (automatic and/or manual in futuro).
            perimeter_wires: OCC wires from contour extraction.
            perimeter_polylines: Optional pre-sampled loops for tests or custom input.
        """
        feature_list = list(features)
        warnings: list[str] = []

        top_projection = self._infer_top_projection(feature_list)
        safe_projection = top_projection + self.safe_z_offset

        operations: list[ToolpathOperation] = []
        operations.extend(self._build_drilling_ops(feature_list, safe_projection))

        loops = [list(loop) for loop in (perimeter_polylines or [])]
        if perimeter_wires:
            try:
                loops.extend(self.wires_to_polylines(perimeter_wires))
            except Exception as exc:  # pragma: no cover - defensive on OCC runtime
                warnings.append(f"Perimeter wire conversion failed: {exc}")

        operations.extend(self._build_profile_ops(loops, safe_projection))

        if not operations:
            warnings.append("No operations generated: insufficient features/perimeter data")

        return ToolpathPlan(
            working_plane_normal=self._to_point(self.working_plane_normal),
            safe_projection=safe_projection,
            operations=operations,
            warnings=warnings,
        )

    def _build_drilling_ops(self, features: Iterable[Any], safe_projection: float) -> list[ToolpathOperation]:
        operations: list[ToolpathOperation] = []

        for idx, feature in enumerate(features):
            if getattr(feature, "type", "") != "hole_group":
                continue
            holes = list(getattr(feature, "holes", []))
            if not holes:
                continue

            through = bool(feature.props.get("through", False))
            group_depth = float(feature.props.get("depth", 0.0) or 0.0)
            diameter = float(feature.props.get("diameter", 0.0) or 0.0)
            breakthrough = 0.5 if through else 0.0

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
                target_point = self._point_at_projection(center, target_projection)

                motions.append(MotionCommand(move="rapid", point=self._to_point(safe_point)))
                motions.append(MotionCommand(move="rapid", point=self._to_point(clearance_point)))
                motions.append(
                    MotionCommand(move="linear", point=self._to_point(target_point), feed=self.plunge_feed)
                )
                motions.append(MotionCommand(move="rapid", point=self._to_point(safe_point)))

            operations.append(
                ToolpathOperation(
                    op_id=f"drill_{idx}",
                    strategy="drilling",
                    feature_type="hole_group",
                    motions=motions,
                    metadata={
                        "hole_count": len(ordered_holes),
                        "diameter": round(diameter, 4),
                        "depth": round(group_depth, 4),
                        "through": through,
                    },
                )
            )

        return operations

    def _build_profile_ops(
        self,
        perimeter_loops: Iterable[Iterable[tuple[float, float, float]]],
        safe_projection: float,
    ) -> list[ToolpathOperation]:
        operations: list[ToolpathOperation] = []

        for idx, loop in enumerate(perimeter_loops):
            points = [np.array(point, dtype=float) for point in loop]
            if len(points) < 3:
                continue

            if np.linalg.norm(points[0] - points[-1]) > 1e-6:
                points.append(points[0])

            start = points[0]
            start_projection = float(np.dot(start, self.working_plane_normal))
            safe_start = self._point_at_projection(start, safe_projection)
            lead_in = self._point_at_projection(start, start_projection + self.clearance_z)

            motions: list[MotionCommand] = [
                MotionCommand(move="rapid", point=self._to_point(safe_start)),
                MotionCommand(move="rapid", point=self._to_point(lead_in)),
                MotionCommand(move="linear", point=self._to_point(start), feed=self.plunge_feed),
            ]
            motions.extend(
                MotionCommand(move="linear", point=self._to_point(point), feed=self.cut_feed)
                for point in points[1:]
            )
            motions.append(MotionCommand(move="rapid", point=self._to_point(safe_start)))

            operations.append(
                ToolpathOperation(
                    op_id=f"profile_{idx}",
                    strategy="2p5d_profile",
                    feature_type="perimeter",
                    motions=motions,
                    metadata={
                        "point_count": len(points),
                    },
                )
            )

        return operations

    def _infer_top_projection(self, features: Iterable[Any]) -> float:
        projections: list[float] = []

        for feature in features:
            if getattr(feature, "type", "") == "hole_group":
                holes = getattr(feature, "holes", [])
                for hole in holes:
                    center = np.array(hole.props.get("center", (0.0, 0.0, 0.0)), dtype=float)
                    projections.append(float(np.dot(center, self.working_plane_normal)))
                continue

            center = feature.props.get("center") if hasattr(feature, "props") else None
            if center is None:
                continue
            center_np = np.array(center, dtype=float)
            projections.append(float(np.dot(center_np, self.working_plane_normal)))

        return max(projections) if projections else 0.0

    def _point_at_projection(self, point: np.ndarray, projection: float) -> np.ndarray:
        current_projection = float(np.dot(point, self.working_plane_normal))
        delta = projection - current_projection
        return point + self.working_plane_normal * delta

    @staticmethod
    def _to_point(point: np.ndarray) -> Point3:
        return (float(point[0]), float(point[1]), float(point[2]))

    @staticmethod
    def wires_to_polylines(wires: Iterable[Any], samples_per_edge: int = 8) -> list[list[tuple[float, float, float]]]:
        """Sample OCC perimeter wires into polyline loops usable by the planner."""
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        polylines: list[list[tuple[float, float, float]]] = []
        edge_samples = max(2, int(samples_per_edge))

        for wire in wires:
            points: list[tuple[float, float, float]] = []
            exp_edges = TopExp_Explorer(wire, TopAbs_EDGE)
            while exp_edges.More():
                curve = BRepAdaptor_Curve(TopoDS.Edge_s(exp_edges.Current()))
                t_first = curve.FirstParameter()
                t_last = curve.LastParameter()
                for sample_idx in range(edge_samples):
                    t = t_first + (t_last - t_first) * sample_idx / (edge_samples - 1)
                    pnt = curve.Value(t)
                    sampled = (float(pnt.X()), float(pnt.Y()), float(pnt.Z()))
                    if not points or np.linalg.norm(np.array(points[-1]) - np.array(sampled)) > 1e-7:
                        points.append(sampled)
                exp_edges.Next()
            if len(points) >= 3:
                if np.linalg.norm(np.array(points[0]) - np.array(points[-1])) > 1e-6:
                    points.append(points[0])
                polylines.append(points)

        return polylines

