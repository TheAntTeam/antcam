"""Motion program builder and validator for legal motion sequences."""

from __future__ import annotations

from antcam_rc2.core.errors import ToolpathError
from antcam_rc2.core.toolpath.models import MotionCommand, MotionKind, MotionProgram, Position3

_CUT_KINDS = frozenset({MotionKind.CUT_LINEAR, MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW})


class MotionBuilder:
    """Builds and validates a motion program incrementally."""

    def __init__(self, operation_id: str) -> None:
        self._motions: list[MotionCommand] = []
        self._operation_id = operation_id
        self._last_endpoint: Position3 | None = None
        self._pass_index = 0
        self._in_cut = False

    @property
    def operation_id(self) -> str:
        return self._operation_id

    @property
    def pass_index(self) -> int:
        return self._pass_index

    @property
    def motion_count(self) -> int:
        """Number of commands appended so far."""
        return len(self._motions)

    def set_pass_index(self, index: int) -> None:
        """Set the pass tag for subsequently appended commands."""
        self._pass_index = index

    def rapid(self, x: float, y: float, z: float) -> None:
        """Append a rapid move to an absolute endpoint."""
        self.append(
            MotionCommand(
                kind=MotionKind.RAPID,
                endpoint=Position3(x_mm=x, y_mm=y, z_mm=z),
                operation_id=self._operation_id,
                pass_index=self._pass_index,
            )
        )

    def cut_linear(self, x: float, y: float, z: float, feed_mm_min: float) -> None:
        """Append a linear cutting move to an absolute endpoint."""
        self.append(
            MotionCommand(
                kind=MotionKind.CUT_LINEAR,
                endpoint=Position3(x_mm=x, y_mm=y, z_mm=z),
                feed_mm_min=feed_mm_min,
                operation_id=self._operation_id,
                pass_index=self._pass_index,
            )
        )

    def cut_arc_cw(
        self,
        x: float,
        y: float,
        z: float,
        center_x: float,
        center_y: float,
        feed_mm_min: float,
    ) -> None:
        """Append a clockwise arc cutting move."""
        self.append(
            MotionCommand(
                kind=MotionKind.CUT_ARC_CW,
                endpoint=Position3(x_mm=x, y_mm=y, z_mm=z),
                feed_mm_min=feed_mm_min,
                arc_center_xy=(center_x, center_y),
                operation_id=self._operation_id,
                pass_index=self._pass_index,
            )
        )

    def cut_arc_ccw(
        self,
        x: float,
        y: float,
        z: float,
        center_x: float,
        center_y: float,
        feed_mm_min: float,
    ) -> None:
        """Append a counter-clockwise arc cutting move."""
        self.append(
            MotionCommand(
                kind=MotionKind.CUT_ARC_CCW,
                endpoint=Position3(x_mm=x, y_mm=y, z_mm=z),
                feed_mm_min=feed_mm_min,
                arc_center_xy=(center_x, center_y),
                operation_id=self._operation_id,
                pass_index=self._pass_index,
            )
        )

    def dwell(self, seconds: float) -> None:
        """Append a dwell at the current position."""
        if seconds <= 0:
            raise ToolpathError("dwell seconds must be positive")
        self.append(
            MotionCommand(
                kind=MotionKind.DWELL,
                endpoint=self._last_endpoint or Position3(),
                dwell_seconds=seconds,
                operation_id=self._operation_id,
                pass_index=self._pass_index,
            )
        )

    def append(self, cmd: MotionCommand) -> None:
        """Append one validated command, checking the transition from the previous one."""
        if self._last_endpoint is not None:
            self._validate_transition(self._last_endpoint, cmd)
        self._motions.append(cmd)
        self._last_endpoint = cmd.endpoint
        self._in_cut = cmd.kind in _CUT_KINDS

    def _validate_transition(self, from_pos: Position3, to_cmd: MotionCommand) -> None:
        """Validate arc transitions.

        Arcs require an explicit XY centre; helical arcs (G2/G3 with a Z
        travel) are allowed and are used by thread milling.
        """
        if to_cmd.kind in {MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW}:
            if to_cmd.arc_center_xy is None:
                raise ToolpathError("arc requires arc_center_xy")

    def build(self) -> MotionProgram:
        """Build the final motion program."""
        if not self._motions:
            raise ToolpathError("no motions in program")
        return MotionProgram(motions=tuple(self._motions))

    def clear(self) -> None:
        """Reset the builder."""
        self._motions.clear()
        self._last_endpoint = None
        self._in_cut = False
