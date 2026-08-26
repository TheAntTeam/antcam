"""Shared geometry helpers and the deterministic contour tracer.

Every strategy consumes native scene entities and emits motions through a
single :class:`ContourTracer`, which owns the entry/plunge/cut/exit and
rapid-link choreography.  This removes the duplicated per-strategy motion
blocks and keeps the motion contract consistent across the 18 operations.
"""

from __future__ import annotations

from antcam_rc2.core.errors import OperationError
from antcam_rc2.core.geometry.curves import Circle, Curve2
from antcam_rc2.core.geometry.paths import Contour, Orientation, Path
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.scene import SceneEntity
from antcam_rc2.core.operations.contracts import StrategyResult
from antcam_rc2.core.toolpath.builder import MotionBuilder
from antcam_rc2.core.toolpath.diagnostics import ToolpathCode
from antcam_rc2.core.toolpath.entry_exit import build_entry_sequence, build_exit_sequence
from antcam_rc2.core.toolpath.models import ToolpathDiagnostic, ToolpathSeverity
from antcam_rc2.core.toolpath.ordering import order_contours_nearest_neighbor

_RAMP_LENGTH_MM = 2.0


def entity_polylines(entity: SceneEntity, tolerance_mm: float) -> list[list[Point2]]:
    """Flatten any scene entity into one or more point polylines.

    A ``Path`` yields one polyline per contour; a ``Contour`` or ``Curve2``
    yields a single polyline.  The return type is always
    ``list[list[Point2]]`` so callers never juggle mixed unions.
    """
    if isinstance(entity, Path):
        return entity.to_polyline(tolerance_mm)
    if isinstance(entity, Contour):
        return [entity.to_polyline(tolerance_mm)]
    if isinstance(entity, Curve2):
        return [entity.to_polyline(tolerance_mm)]
    raise OperationError(f"unsupported geometry for operation: {type(entity).__name__}")


def closed_contours(entity: SceneEntity) -> tuple[Contour, ...]:
    """Return the closed contours of an entity, rejecting open geometry."""
    if isinstance(entity, Path):
        contours = tuple(entity.contours)
    elif isinstance(entity, Contour):
        contours = (entity,)
    else:
        contours = ()
    if not contours or any(not contour.closed for contour in contours):
        raise OperationError("operation requires closed Contour or Path geometry")
    return contours


def inward_distance(contour: Contour, distance_mm: float) -> float:
    """Signed offset distance: negative (inward) for CCW external loops."""
    return -distance_mm if contour.orientation() is Orientation.CCW else distance_mm


def drill_center(entity: SceneEntity) -> Point2:
    """Extract the drill target centre from a circle/curve/contour entity."""
    if isinstance(entity, Circle):
        return entity.center
    if isinstance(entity, Curve2):
        return entity.start
    if isinstance(entity, Contour):
        return entity.start
    raise OperationError(f"unsupported geometry for drilling operation: {type(entity).__name__}")


def drill_centers(entities: tuple[SceneEntity, ...]) -> tuple[Point2, ...]:
    """All distinct drill centres of an operation in stable input order."""
    centers: list[Point2] = []
    for entity in entities:
        center = drill_center(entity)
        if all(center.distance_to(existing) > 1e-9 for existing in centers):
            centers.append(center)
    return tuple(centers)


class ContourTracer:
    """Emit deterministic rapid/plunge/cut/exit sequences for ordered loops.

    Loops are ordered with deterministic nearest-neighbour within the same
    depth pass.  Every loop starts with a rapid to clearance above its start
    point, plunges vertically (or ramps when the strategy verifies space),
    cuts the flattened polyline at the pass depth, then retracts to clearance.

    The tracer collects non-fatal warnings (e.g. a degraded ramp entry) that
    the strategy attaches to its :class:`StrategyResult`.
    """

    __slots__ = (
        "_builder",
        "_operation_id",
        "_plunge_feed",
        "_cut_feed",
        "_clearance_z",
        "_tolerance_mm",
        "_ramp_enabled",
        "_warnings",
    )

    def __init__(
        self,
        *,
        builder: MotionBuilder,
        operation_id: str,
        plunge_feed_mm_min: float,
        cut_feed_mm_min: float,
        clearance_z_mm: float,
        tolerance_mm: float,
        ramp_enabled: bool = False,
    ) -> None:
        self._builder = builder
        self._operation_id = operation_id
        self._plunge_feed = plunge_feed_mm_min
        self._cut_feed = cut_feed_mm_min
        self._clearance_z = clearance_z_mm
        self._tolerance_mm = tolerance_mm
        self._ramp_enabled = ramp_enabled
        self._warnings: list[str] = []

    @property
    def warnings(self) -> tuple[str, ...]:
        """Non-fatal warnings collected while tracing."""
        return tuple(self._warnings)

    def add_warning(self, message: str) -> None:
        """Record a non-fatal warning attached to this operation result."""
        self._warnings.append(message)

    def trace_loops(self, loops: list[Contour] | tuple[Contour, ...], depth_z_mm: float) -> None:
        """Cut every loop at ``depth_z_mm``, ordering loops deterministically."""
        if not loops:
            raise OperationError("operation produced no usable loops")
        ordered, _ = order_contours_nearest_neighbor(list(loops))
        for loop in ordered:
            self._trace_polyline(loop.to_polyline(self._tolerance_mm), depth_z_mm)

    def trace_polylines(self, polylines: list[list[Point2]], depth_z_mm: float) -> None:
        """Cut open or closed polylines at ``depth_z_mm`` in input order."""
        if not polylines:
            raise OperationError("operation produced no usable polylines")
        for polyline in polylines:
            self._trace_polyline(polyline, depth_z_mm)

    def _trace_polyline(self, points: list[Point2], depth_z_mm: float) -> None:
        if len(points) < 2:
            return
        start = points[0]
        for motion in build_entry_sequence(
            start_xy=start,
            clearance_z=self._clearance_z,
            depth_z=depth_z_mm,
            plunge_feed=self._plunge_feed,
            use_ramp=self._ramp_enabled,
            ramp_length_mm=_RAMP_LENGTH_MM,
        ):
            self._builder.append(motion)
        for point in points[1:]:
            self._builder.cut_linear(point.x, point.y, depth_z_mm, self._cut_feed)
        end = points[-1]
        for motion in build_exit_sequence(end_xy=end, clearance_z=self._clearance_z, depth_z=depth_z_mm):
            self._builder.append(motion)

    def finish(self) -> StrategyResult:
        """Finalize the program and attach collected warnings as diagnostics."""
        program = self._builder.build()
        diagnostics = tuple(
            ToolpathDiagnostic(
                code=ToolpathCode.ENTRY_DEGRADED.value,
                message=message,
                severity=ToolpathSeverity.WARNING,
                operation_id=self._operation_id,
            )
            for message in self._warnings
        )
        return StrategyResult(program=program, diagnostics=diagnostics)
