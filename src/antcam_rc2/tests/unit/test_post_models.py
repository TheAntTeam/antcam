"""Tests for the post models, tool table, registry and validator."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from antcam_rc2.core.errors import PostProcessorError
from antcam_rc2.core.post import (
    GCodeProgram,
    PostSettings,
    ToolEntry,
    ToolTable,
    build_standard_posts,
    validate,
)
from antcam_rc2.core.units import UnitSystem


def test_post_settings_validation() -> None:
    assert PostSettings().decimals == 3
    assert PostSettings().units is UnitSystem.METRIC
    with pytest.raises(ValidationError):
        PostSettings(decimals=-1)
    with pytest.raises(ValidationError):
        PostSettings(decimals=9)
    with pytest.raises(ValidationError):
        PostSettings(arc_format="ijkx")


def test_tool_table_numbering_and_lookup() -> None:
    table = ToolTable(
        entries=(
            ToolEntry(tool_id="a", tool_number=1, name="A", diameter_mm=3.0),
            ToolEntry(tool_id="b", tool_number=2, name="B", diameter_mm=6.0),
        )
    )
    assert table.number_for("a") == 1
    assert table.number_for("b") == 2
    assert table.by_number(2).tool_id == "b"
    assert table.by_number(99) is None
    with pytest.raises(KeyError):
        table.number_for("missing")


def test_gcode_program_text_and_fingerprint() -> None:
    program = GCodeProgram(
        post_id="grbl",
        plan_fingerprint="sha256:abc",
        settings=PostSettings(),
        lines=("G90", "G1 X1 Y2 Z3 F100"),
        motion_line_count=1,
    )
    assert program.text() == "G90\nG1 X1 Y2 Z3 F100"
    assert program.fingerprint() == program.fingerprint()
    assert program.fingerprint().startswith("sha256:")
    crlf = program.model_copy(update={"settings": PostSettings(line_ending="crlf")})
    assert crlf.text() == "G90\r\nG1 X1 Y2 Z3 F100"


def test_registry_standard_posts_and_duplicates() -> None:
    from antcam_rc2.core.post.grbl import GrblPost

    registry = build_standard_posts()
    assert set(registry.ids()) == {"grbl", "linuxcnc", "makera"}
    assert registry.get("grbl").post_id == "grbl"
    with pytest.raises(PostProcessorError, match="not registered"):
        registry.get("unknown")
    with pytest.raises(PostProcessorError, match="already registered"):
        registry.register(GrblPost)


def test_validator_accepts_valid_program() -> None:
    program = GCodeProgram(
        post_id="grbl",
        plan_fingerprint="sha256:abc",
        settings=PostSettings(),
        lines=("G90", "G21", "G17", "G0 X0 Y0 Z10", "G1 X5 Y5 Z0 F100", "G3 X5 Y5 Z0 I0 J-5", "M5", "M2"),
        motion_line_count=3,
    )
    assert validate(program) == ()


def test_validator_flags_arc_without_ij() -> None:
    program = GCodeProgram(
        post_id="grbl",
        plan_fingerprint="sha256:abc",
        settings=PostSettings(),
        lines=("G1 X5 Y5 Z0 F100", "G2 X10 Y5 Z0"),
        motion_line_count=2,
    )
    problems = validate(program)
    assert any("arc without I/J" in problem for problem in problems)


def test_validator_flags_cut_without_feed() -> None:
    program = GCodeProgram(
        post_id="grbl",
        plan_fingerprint="sha256:abc",
        settings=PostSettings(),
        lines=("G1 X5 Y5 Z0", "G1 X10 Y5 Z0 F100", "G1 X15 Y5 Z0"),
        motion_line_count=3,
    )
    problems = validate(program)
    assert any("without an active feed" in problem for problem in problems)


def test_validator_flags_motion_count_mismatch() -> None:
    program = GCodeProgram(
        post_id="grbl",
        plan_fingerprint="sha256:abc",
        settings=PostSettings(),
        lines=("G0 X0 Y0 Z10", "G1 X5 Y5 Z0 F100"),
        motion_line_count=5,
    )
    assert any("declared motion lines" in problem for problem in validate(program))


def test_validator_handles_comments_and_modal_continuations() -> None:
    program = GCodeProgram(
        post_id="linuxcnc",
        plan_fingerprint="sha256:abc",
        settings=PostSettings(),
        lines=("( header )", "G90", "G0 X0 Y0 Z10", "X5 Y5 Z10", "G1 X5 Y5 Z0 F100", "M5"),
        motion_line_count=3,
    )
    assert validate(program) == ()
