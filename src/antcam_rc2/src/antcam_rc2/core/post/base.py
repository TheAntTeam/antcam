"""Shared modal-state G-code engine and the base post-processor.

The engine emits minimal-but-safe G-code: G0/G1/G2/G3 are modal (emitted only
on change), F is modal (emitted only when the feed changes), X/Y/Z are always
explicit absolute words, I/J are relative to the arc start.  Vendor-specific
differences are declared by subclasses through small hooks; the motion loop,
tool changes, spindle/coolant and deterministic formatting live here once.
"""

from __future__ import annotations

import math

from antcam_rc2.core.post.models import GCodeProgram, PostContext, PostSettings
from antcam_rc2.core.toolpath.models import MotionCommand, MotionKind, Position3
from antcam_rc2.core.units import UnitSystem


class ModalEngine:
    """Tracks the modal G-code state and emits one line per motion."""

    def __init__(self, settings: PostSettings) -> None:
        self._settings = settings
        self._motion_modal: str | None = None  # "G0" | "G1" | "G2" | "G3"
        self._feed: float | None = None
        self._spindle_rpm: float | None = None
        self._spindle_on = False
        self._coolant_active: str | None = None
        self._position: tuple[float, float, float] | None = None
        self._units_emitted = False

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        """Reset modal state after a tool change (position unknown)."""
        self._motion_modal = None
        self._feed = None
        self._spindle_on = False
        self._spindle_rpm = None
        self._coolant_active = None
        self._position = None

    @property
    def position(self) -> tuple[float, float, float] | None:
        """The tracked machine position as ``(x, y, z)``."""
        return self._position

    def set_position(self, x: float, y: float, z: float) -> None:
        """Update the tracked machine position without emitting a line."""
        self._position = (x, y, z)

    def setup_lines(self) -> list[str]:
        """The declared initial state (G90 absolute, units, XY plane)."""
        lines = ["G90"]
        lines.append("G21" if self._settings.units is UnitSystem.METRIC else "G20")
        lines.append("G17")
        self._units_emitted = True
        return lines

    def format_number(self, value: float) -> str:
        """Canonical number formatting: fixed decimals, no trailing zeros."""
        if value == 0.0:
            return "0"
        if value.is_integer():
            return str(int(value))  # integer fast path (common: Z heights, tool points)
        text = f"{value:.{self._settings.decimals}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        if text in ("", "-0"):
            return "0"
        return text

    def _word(self, letter: str, value: float) -> str:
        return f"{letter}{self.format_number(value)}"

    # ------------------------------------------------------------------ motion
    def rapid(self, x: float, y: float, z: float) -> str:
        words = self._motion_words("G0", x, y, z)
        return " ".join(words)

    def cut_linear(self, x: float, y: float, z: float, feed: float) -> str:
        words = self._motion_words("G1", x, y, z)
        feed_word = self._feed_word(feed)
        if feed_word:
            words.append(feed_word)
        return " ".join(words)

    def cut_arc(
        self,
        *,
        ccw: bool,
        x: float,
        y: float,
        z: float,
        center_x: float,
        center_y: float,
        feed: float,
    ) -> str:
        code = "G3" if ccw else "G2"
        words = self._motion_words(code, x, y, z)
        start = self._position
        if start is None:
            raise ValueError("arc requires a known start position")
        words.extend(self._arc_words(center_x - start[0], center_y - start[1]))
        feed_word = self._feed_word(feed)
        if feed_word:
            words.append(feed_word)
        return " ".join(words)

    def dwell(self, seconds: float) -> str:
        value = seconds if self._settings.dwell_units == "s" else seconds * 1000.0
        self._motion_modal = None  # a dwell resets the motion modal in our model
        return f"G4 P{self.format_number(value)}"

    def spindle_on(self, rpm: float) -> str | None:
        if self._spindle_on and self._spindle_rpm == rpm:
            return None
        self._spindle_on = True
        self._spindle_rpm = rpm
        return f"M3 S{self.format_number(rpm)}"

    def spindle_off(self) -> str | None:
        if not self._spindle_on:
            return None
        self._spindle_on = False
        self._spindle_rpm = None
        return "M5"

    def coolant(self, mcode: str | None) -> str | None:
        """Enable a coolant M-code, or clear coolant when ``mcode`` is None."""
        if mcode == self._coolant_active:
            return None
        self._coolant_active = mcode
        if mcode is None:
            return "M9"
        return mcode

    # ------------------------------------------------------------------ internals
    def _motion_words(self, code: str, x: float, y: float, z: float) -> list[str]:
        words: list[str] = []
        if self._motion_modal != code:
            words.append(code)
            self._motion_modal = code
        words.append(self._word("X", x))
        words.append(self._word("Y", y))
        words.append(self._word("Z", z))
        self._position = (x, y, z)
        return words

    def _feed_word(self, feed: float) -> str:
        if self._feed is None or abs(self._feed - feed) > 1e-9:
            self._feed = feed
            return self._word("F", feed)
        return ""

    def _arc_words(self, center_dx: float, center_dy: float) -> list[str]:
        if self._settings.arc_format == "ijk":
            return [self._word("I", center_dx), self._word("J", center_dy)]
        radius = math.hypot(center_dx, center_dy)
        return [self._word("R", radius)]


class BasePostProcessor:
    """Shared conversion pipeline; vendors override the small hooks."""

    post_id: str = "base"
    version: str = "1.0"

    # ------------------------------------------------------------------ hooks
    def comment(self, text: str) -> str:
        raise NotImplementedError

    def header_lines(self, ctx: PostContext, engine: ModalEngine) -> list[str]:
        """Vendor-specific header lines (after the shared setup)."""
        return []

    def footer_lines(self, ctx: PostContext, engine: ModalEngine) -> list[str]:
        """Shared footer: vertical safe retract, spindle off, coolant off."""
        lines: list[str] = []
        safe_z = self._safe_retract(ctx)
        position = engine.position
        if safe_z is not None and position is not None and abs(safe_z - position[2]) > 1e-9:
            lines.append(engine.rapid(position[0], position[1], safe_z))
        spindle_off = engine.spindle_off()
        if spindle_off:
            lines.append(spindle_off)
        coolant_off = engine.coolant(None)
        if coolant_off:
            lines.append(coolant_off)
        return lines

    def _safe_retract(self, ctx: PostContext) -> float | None:
        """The retract Z: explicit setting or the plan clearance above stock top."""
        settings = ctx.settings
        if settings.safe_retract_z_mm is not None:
            return settings.safe_retract_z_mm
        stock = ctx.project.stock
        top = stock.position_z_mm + ctx.project.wcs.offset_z_mm + stock.height_mm
        clearance = ctx.plan.settings.clearance_z_mm if ctx.plan.settings is not None else 5.0
        return top + clearance

    def tool_change_line(self, tool_number: int) -> str:
        raise NotImplementedError

    def end_line(self) -> str:
        raise NotImplementedError

    def coolant_mcode(self, ctx: PostContext, cooling_id: str) -> str | None:
        return ctx.settings.coolant_mcodes.get(cooling_id)

    # ------------------------------------------------------------------ convert
    def convert(self, ctx: PostContext) -> GCodeProgram:
        settings = ctx.settings
        engine = ModalEngine(settings)
        lines: list[str] = []
        motion_count = 0

        if settings.emit_comments:
            lines.append(self.comment(f"AntCAM RC2 — post {self.post_id} v{self.version}"))
            lines.append(self.comment(f"program: {settings.program_id or ctx.plan_fingerprint[:12]}"))
            lines.append(self.comment(f"machine: {ctx.machine.name}"))
        lines.extend(engine.setup_lines())
        lines.extend(self.header_lines(ctx, engine))
        lines.extend(settings.header_extra_lines)

        current_tool: int | None = None
        for result in ctx.plan.operations:
            if result.program is None:
                continue
            meta = ctx.operation_meta.get(result.operation_id)
            if meta is None:
                continue
            if settings.emit_comments:
                lines.append(self.comment(f"--- operation {meta.operation_id} ({meta.name}) ---"))

            tool_number = ctx.tool_table.number_for(meta.tool_id)
            if settings.tool_change_policy == "auto" and tool_number != current_tool:
                lines.append(self.tool_change_line(tool_number))
                engine.reset()
                current_tool = tool_number

            spindle = engine.spindle_on(meta.rpm or 0.0)
            if spindle:
                lines.append(spindle)
            coolant = engine.coolant(self.coolant_mcode(ctx, meta.cooling_id))
            if coolant:
                lines.append(coolant)

            pending: Position3 | None = None  # merged-rapid target
            for motion in result.program.motions:
                if motion.kind is MotionKind.RAPID:
                    if settings.merge_consecutive_rapids and pending is not None and _same_z(pending, motion.endpoint):
                        pending = motion.endpoint
                        engine.set_position(pending.x_mm, pending.y_mm, pending.z_mm)
                        continue
                    if pending is not None:
                        lines.append(engine.rapid(pending.x_mm, pending.y_mm, pending.z_mm))
                        motion_count += 1
                    pending = motion.endpoint
                    engine.set_position(pending.x_mm, pending.y_mm, pending.z_mm)
                    continue
                if pending is not None:
                    lines.append(engine.rapid(pending.x_mm, pending.y_mm, pending.z_mm))
                    motion_count += 1
                    pending = None
                line = self._emit_motion(engine, motion, settings)
                if line:
                    lines.append(line)
                    motion_count += 1
            if pending is not None:
                lines.append(engine.rapid(pending.x_mm, pending.y_mm, pending.z_mm))
                motion_count += 1

        lines.extend(self.footer_lines(ctx, engine))
        lines.extend(settings.footer_extra_lines)
        if settings.emit_comments:
            lines.append(self.comment(f"end — {motion_count} motion lines"))
        lines.append(self.end_line())

        return GCodeProgram(
            post_id=self.post_id,
            plan_fingerprint=ctx.plan_fingerprint,
            settings=settings,
            lines=tuple(lines),
            motion_line_count=motion_count,
        )

    def _emit_motion(
        self,
        engine: ModalEngine,
        motion: MotionCommand,
        settings: PostSettings,
    ) -> str | None:
        feed = motion.feed_mm_min or 0.0
        if settings.max_feed_mm_min is not None:
            feed = min(feed, settings.max_feed_mm_min)
        endpoint = motion.endpoint
        if motion.kind is MotionKind.CUT_LINEAR:
            return engine.cut_linear(endpoint.x_mm, endpoint.y_mm, endpoint.z_mm, feed)
        if motion.kind in {MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW}:
            assert motion.arc_center_xy is not None
            return engine.cut_arc(
                ccw=motion.kind is MotionKind.CUT_ARC_CCW,
                x=endpoint.x_mm,
                y=endpoint.y_mm,
                z=endpoint.z_mm,
                center_x=motion.arc_center_xy[0],
                center_y=motion.arc_center_xy[1],
                feed=feed,
            )
        if motion.kind is MotionKind.DWELL:
            return engine.dwell(motion.dwell_seconds or 0.0)
        return None


def _same_z(a: Position3, b: Position3) -> bool:
    return abs(a.z_mm - b.z_mm) <= 1e-9


__all__ = ["BasePostProcessor", "ModalEngine"]
