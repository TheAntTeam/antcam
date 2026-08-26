from pathlib import Path
import sys

from types import SimpleNamespace

import run_main_viewer


def test_resolve_input_path_keeps_repo_relative_paths():
    resolved = Path(run_main_viewer._resolve_input_path("run_main_viewer.py"))

    assert resolved == (run_main_viewer.REPO_ROOT / "run_main_viewer.py").resolve()


def test_resolve_input_path_falls_back_to_tests_data_for_bare_sample_name():
    resolved = Path(run_main_viewer._resolve_input_path("flange.step"))

    assert resolved == (run_main_viewer.SAMPLE_DATA_ROOT / "flange.step").resolve()


def test_main_forwards_profile_rough_only_to_planner_config(monkeypatch):
    captured = {}

    class _FakePlannerRuntimeConfig:
        def __init__(self, **kwargs):
            captured["planner_kwargs"] = kwargs

    monkeypatch.setitem(sys.modules, "antcam.main_viewer", SimpleNamespace(run_feature_viewer=lambda *args, **kwargs: None))
    monkeypatch.setitem(sys.modules, "antcam.planner_config", SimpleNamespace(PlannerRuntimeConfig=_FakePlannerRuntimeConfig))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_main_viewer.py",
            "flange.step",
            "--profile-stock-allowance",
            "0.25",
            "--profile-rough-only",
        ],
    )

    exit_code = run_main_viewer.main()

    assert exit_code == 0
    assert captured["planner_kwargs"]["profile_rough_only"] is True
    assert captured["planner_kwargs"]["profile_stock_allowance"] == 0.25