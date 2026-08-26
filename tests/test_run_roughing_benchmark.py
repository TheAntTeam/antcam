import sys

from pathlib import Path
from types import SimpleNamespace

import run_roughing_benchmark


def test_main_forwards_analysis_bundle_to_benchmark_viewer(monkeypatch):
    captured = {}

    class _FakePlannerRuntimeConfig:
        def __init__(self, **kwargs):
            captured["planner_kwargs"] = kwargs

    def _build_piece_roughing_analysis(file_path, **kwargs):
        captured["analysis_path"] = file_path
        captured["analysis_kwargs"] = kwargs
        return SimpleNamespace(
            model="model",
            features=[],
            diagnostics={"feature_count": 0, "skip_reason": "piece_roughing_only"},
            working_plane_normal=(0.0, 0.0, 1.0),
            contour_shadow="shadow",
            perimeter=["wire_1"],
            vertical_arc_groups=[{"id": 1}],
            toolpath_plan=SimpleNamespace(operations=[], warnings=[]),
            planner_config="planner_config",
        )

    def _show_roughing_benchmark(model, features, **kwargs):
        captured["show"] = {
            "model": model,
            "features": features,
            "kwargs": kwargs,
        }

    monkeypatch.setitem(
        sys.modules,
        "antcam.main_viewer",
        SimpleNamespace(build_piece_roughing_analysis=_build_piece_roughing_analysis),
    )
    monkeypatch.setitem(sys.modules, "antcam.benchmark_viewer", SimpleNamespace(show_roughing_benchmark=_show_roughing_benchmark))
    monkeypatch.setitem(sys.modules, "antcam.planner_config", SimpleNamespace(PlannerRuntimeConfig=_FakePlannerRuntimeConfig))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_roughing_benchmark.py",
            "flange.step",
            "--profile-stock-allowance",
            "0.25",
            "--profile-rough-only",
        ],
    )

    exit_code = run_roughing_benchmark.main()

    assert exit_code == 0
    assert captured["planner_kwargs"]["profile_rough_only"] is True
    assert captured["planner_kwargs"]["piece_roughing_only"] is True
    assert captured["planner_kwargs"]["profile_stock_allowance"] == 0.25
    assert Path(captured["analysis_path"]) == (run_roughing_benchmark.run_main_viewer.SAMPLE_DATA_ROOT / "flange.step").resolve()
    assert captured["show"]["model"] == "model"
    assert captured["show"]["features"] == []
    assert captured["show"]["kwargs"]["diagnostics"] == {"feature_count": 0, "skip_reason": "piece_roughing_only"}
    assert captured["show"]["kwargs"]["toolpath_plan"].warnings == []