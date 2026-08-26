from pathlib import Path
import sys

from types import SimpleNamespace

import run_piece_toolpath_preview


def test_main_forwards_analysis_bundle_to_piece_toolpath_viewer(monkeypatch):
    captured = {}

    class _FakePlannerRuntimeConfig:
        def __init__(self, **kwargs):
            captured["planner_kwargs"] = kwargs

    def _run_piece_toolpath_preview(file_path, **kwargs):
        captured["file_path"] = file_path
        captured["kwargs"] = kwargs

    monkeypatch.setitem(sys.modules, "antcam.main_viewer", SimpleNamespace(run_piece_toolpath_preview=_run_piece_toolpath_preview))
    monkeypatch.setitem(sys.modules, "antcam.planner_config", SimpleNamespace(PlannerRuntimeConfig=_FakePlannerRuntimeConfig))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_piece_toolpath_preview.py",
            "flange.step",
            "--max-stepdown",
            "3.5",
            "--tool-diameter",
            "8.0",
        ],
    )

    exit_code = run_piece_toolpath_preview.main()

    assert exit_code == 0
    assert captured["planner_kwargs"]["max_stepdown"] == 3.5
    assert captured["planner_kwargs"]["tool_diameter"] == 8.0
    assert Path(captured["file_path"]) == (run_piece_toolpath_preview.run_main_viewer.SAMPLE_DATA_ROOT / "flange.step").resolve()