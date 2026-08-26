"""End-to-end demo command: import -> project -> plan -> simulate -> post on a bundled sample."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from antcam_rc2.__main__ import main

SAMPLES = ("mech_plate", "pcb_panel", "imperial_part")


@pytest.mark.parametrize("sample", SAMPLES)
def test_demo_command_produces_all_outputs(tmp_path: Path, sample: str) -> None:
    outdir = tmp_path / "out"
    exit_code = main(
        [
            "demo",
            "--sample",
            sample,
            "--outdir",
            str(outdir),
            "--resolution",
            "1.0",
            "--clearance-z",
            "5",
        ]
    )

    assert exit_code == 0
    plan = json.loads((outdir / "plan.json").read_text(encoding="utf-8"))
    report = json.loads((outdir / "report.json").read_text(encoding="utf-8"))
    gcode = (outdir / "output.nc").read_text(encoding="utf-8")

    assert plan["is_executable"] is True
    assert plan["operations"][0]["status"] == "succeeded"
    assert report["stats"]["removed_voxels"] > 0
    assert report["stats"]["collision_count"] == 0
    assert report["events"] == [], f"demo should be warning-free, got {report['events']}"
    assert gcode.startswith("; AntCAM RC2")
    assert "G90" in gcode


def test_demo_unknown_sample_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["demo", "--sample", "nope", "--outdir", str(tmp_path)])
