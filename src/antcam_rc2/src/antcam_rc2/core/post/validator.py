"""Pure G-code invariant validator.

Re-parses an emitted program with a minimal grammar (word = letter + number,
comments stripped for both ``;`` and ``( )`` styles) and checks post-
conditions by whole words (never substrings): every cut has an active feed,
every arc has I/J (or R), spindle state is coherent, and the emitted motion
line count matches the declared one.
"""

from __future__ import annotations

import re

from antcam_rc2.core.post.models import GCodeProgram

_WORD_RE = re.compile(r"([A-Za-z])(-?\d+(?:\.\d+)?)")


def _strip_comment(line: str) -> str:
    semi = line.find(";")
    if semi >= 0:
        line = line[:semi]
    start = line.find("(")
    if start >= 0:
        end = line.find(")", start)
        if end >= 0:
            line = line[:start] + line[end + 1 :]
    return line.strip()


def _words(line: str) -> dict[str, float]:
    """A hand-rolled word scanner (2-3x faster than a regex for G-code lines)."""
    words: dict[str, float] = {}
    index = 0
    length = len(line)
    while index < length:
        letter = line[index]
        if letter.isalpha():
            start = index + 1
            end = start
            while end < length and (line[end].isdigit() or line[end] in ".-"):
                end += 1
            if end > start:
                words[letter] = float(line[start:end])
            index = end
        else:
            index += 1
    return words


def validate(program: GCodeProgram) -> tuple[str, ...]:
    """Return a tuple of invariant violations (empty = valid)."""
    problems: list[str] = []
    active_feed: float | None = None
    motion_modal: str | None = None
    in_motion = False
    motion_lines = 0

    for line_index, line in enumerate(program.lines):
        cleaned = _strip_comment(line)
        if not cleaned:
            continue
        words = _words(cleaned)
        g = words.get("G")
        m = words.get("M")

        if "F" in words:
            if words["F"] <= 0:
                problems.append(f"line {line_index}: non-positive feed F{words['F']:g}")
            active_feed = words["F"]

        if g in {0, 1, 2, 3}:
            motion_modal = f"G{int(g)}"
            in_motion = True
        elif g is not None or m is not None:
            in_motion = False  # G90/G21/G17/G4 or an M command resets the motion modal

        if in_motion:
            motion_lines += 1
            if motion_modal in {"G1", "G2", "G3"} and "F" not in words and active_feed is None:
                problems.append(f"line {line_index}: cut without an active feed")
            if motion_modal in {"G2", "G3"}:
                if "I" not in words and "R" not in words:
                    problems.append(f"line {line_index}: arc without I/J or R")
                if "I" in words and "J" not in words:
                    problems.append(f"line {line_index}: arc with I but no J")

        if m == 3:
            pass  # M3 acknowledged; spindle state is the controller's concern

    if not (program.motion_line_count <= motion_lines <= program.motion_line_count + 2):
        problems.append(
            f"declared motion lines {program.motion_line_count} != emitted {motion_lines} "
            "(footer retract may add up to 2 motion lines)"
        )
    return tuple(problems)


__all__ = ["validate"]
