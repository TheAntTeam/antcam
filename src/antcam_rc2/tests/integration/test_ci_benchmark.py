"""Smoke test: the benchmark harness runs and reports (no timing assertions)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_TOOLS = Path(__file__).parent.parent.parent / "tools"


def test_benchmark_harness_runs_all_scenarios() -> None:
    """The harness must execute every scenario without crashing."""
    result = subprocess.run(
        [sys.executable, str(_TOOLS / "benchmark.py"), "--runs", "1"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr
    for scenario in (
        "plan_50_ops",
        "plan_stress_first",
        "plan_stress_replan",
        "simulate_20_ops",
        "post_12k_motion",
        "render_5k_entities",
    ):
        assert scenario in result.stdout, f"missing scenario {scenario} in output"


def test_benchmark_harness_supports_single_scenario() -> None:
    result = subprocess.run(
        [sys.executable, str(_TOOLS / "benchmark.py"), "--scenario", "plan_50_ops", "--runs", "1"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert "plan_50_ops" in result.stdout
    assert "plan_stress" not in result.stdout
