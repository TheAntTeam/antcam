"""Golden tests for the LinuxCNC and Makera post-processors."""

from __future__ import annotations

from antcam_rc2.core.post import PostService, PostSettings


def _lines(application, project, plan, post_id: str, **overrides) -> list[str]:
    service = PostService(application.catalog_repository)
    settings = PostSettings(program_id="mini", **overrides)
    return list(service.post(project, plan, settings=settings, post_id=post_id).lines)


def test_linuxcnc_golden_output(application, post_mini_plan) -> None:
    project, plan = post_mini_plan
    lines = _lines(application, project, plan, "linuxcnc")
    assert lines == [
        "( AntCAM RC2 — post linuxcnc v1.0 )",
        "( program: mini )",
        "( machine: Makera Z1 )",
        "G90",
        "G21",
        "G17",
        "( --- operation op_1234abcd (mini) --- )",
        "T1 M6",
        "M3 S12000",
        "G0 X5 Y5 Z15",
        "G1 X5 Y5 Z9 F208",
        "X15 Y5 Z9 F520",
        "G3 X15 Y5 Z9 I0 J-5",
        "G0 X15 Y5 Z15",
        "M5",
        "( end — 5 motion lines )",
        "M2",
    ]


def test_makera_golden_output(application, post_mini_plan) -> None:
    project, plan = post_mini_plan
    lines = _lines(application, project, plan, "makera")
    assert lines == [
        "; AntCAM RC2 — post makera v1.0",
        "; program: mini",
        "; machine: Makera Z1",
        "G90",
        "G21",
        "G17",
        "; Makera Z1 — mini",
        "; --- operation op_1234abcd (mini) ---",
        "M6 T1",
        "M3 S12000",
        "G0 X5 Y5 Z15",
        "G1 X5 Y5 Z9 F208",
        "X15 Y5 Z9 F520",
        "G3 X15 Y5 Z9 I0 J-5",
        "G0 X15 Y5 Z15",
        "M5",
        "; end — 5 motion lines",
        "M2",
    ]


def test_makera_aerodust_is_comment_only_by_default(application, post_mini_plan) -> None:
    project, plan = post_mini_plan
    lines = _lines(application, project, plan, "makera")
    # AeroDust maps to None by default: no coolant M-code is emitted.
    assert not any(line.startswith("M7") or line.startswith("M8") for line in lines)


def test_makera_emits_coolant_when_configured(application, post_mini_plan) -> None:
    project, plan = post_mini_plan
    lines = _lines(application, project, plan, "makera", coolant_mcodes={"aerodust": "M7", "flood": "M8", "mist": "M7"})
    assert "M7" in lines
