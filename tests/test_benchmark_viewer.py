from types import SimpleNamespace

import pytest

from antcam.benchmark_viewer import RoughingBenchmarkWindow
from antcam.viewer import AntCamViewerWindow


def _build_benchmark_window_for_logic_tests():
    win = RoughingBenchmarkWindow.__new__(RoughingBenchmarkWindow)
    win._selected_operation_id = None
    win._benchmark_diagnostics = {}
    win._benchmark_planner_config = None
    win._toolpath_plan = None
    win.features = []
    win._roughing_summary_by_id = {}
    win._roughing_operations_by_id = {}
    win._toolpath_operation_mode = AntCamViewerWindow._toolpath_operation_mode.__get__(win, RoughingBenchmarkWindow)
    win._split_toolpath_motion_paths = AntCamViewerWindow._split_toolpath_motion_paths.__get__(win, RoughingBenchmarkWindow)
    return win


def test_build_roughing_operation_summaries_filters_non_roughing_and_computes_metrics():
    win = _build_benchmark_window_for_logic_tests()

    roughing = SimpleNamespace(
        op_id="cavity_0_rough",
        strategy="cavity_clearing",
        feature_type="pocket",
        metadata={
            "operation_mode": "roughing",
            "clearing_style": "contour_parallel",
            "pass_count": 3,
            "path_count": 5,
            "tool_id": "endmill_6",
            "tool_diameter": 6.0,
            "cut_feed": 300.0,
            "plunge_feed": 120.0,
            "machining_class": "pocket_milling",
            "feature_subtype": "closed_pocket",
            "geometry_source": "feature_boundary_loops",
        },
        motions=[
            SimpleNamespace(move="rapid", point=(0.0, 0.0, 5.0)),
            SimpleNamespace(move="linear", point=(0.0, 0.0, 0.0), feed=120.0),
            SimpleNamespace(move="linear", point=(3.0, 4.0, 0.0), feed=300.0),
        ],
    )
    finishing = SimpleNamespace(
        op_id="cavity_0_finish",
        strategy="cavity_clearing",
        feature_type="pocket",
        metadata={"operation_mode": "finishing"},
        motions=[],
    )

    summaries = win._build_roughing_operation_summaries([roughing, finishing])

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.op_id == "cavity_0_rough"
    assert summary.style == "contour_parallel"
    assert summary.pass_count == 3
    assert summary.path_count == 5
    assert summary.cut_segment_count == 1
    assert summary.cut_length == pytest.approx(5.0)
    assert summary.estimated_cut_minutes == pytest.approx(5.0 / 300.0)


def test_should_display_toolpath_operation_filters_to_roughing_and_selected_operation():
    win = _build_benchmark_window_for_logic_tests()

    roughing_a = SimpleNamespace(op_id="slot_0_rough", metadata={"operation_mode": "roughing"})
    roughing_b = SimpleNamespace(op_id="cavity_0_rough", metadata={"operation_mode": "roughing"})
    finishing = SimpleNamespace(op_id="slot_0_finish", metadata={"operation_mode": "finishing"})

    assert win._should_display_toolpath_operation(roughing_a) is True
    assert win._should_display_toolpath_operation(finishing) is False

    win._selected_operation_id = "cavity_0_rough"

    assert win._should_display_toolpath_operation(roughing_a) is False
    assert win._should_display_toolpath_operation(roughing_b) is True


def test_build_diagnostics_text_includes_raw_candidates_counts_and_warnings():
    win = _build_benchmark_window_for_logic_tests()
    win.features = [SimpleNamespace(type="pocket", props={"depth": 4.0})]
    win._benchmark_diagnostics = {
        "has_brep": True,
        "raw_candidates": {"cylinders": 2, "cones": 1, "arc_groups": 3},
        "feature_count": 1,
        "feature_types": {"pocket": 1},
        "piece_projection_bounds": {"top": 8.0, "bottom": 0.0, "depth": 8.0},
    }
    win._benchmark_planner_config = SimpleNamespace(
        parameter_mode="automatic",
        tool_library="standard_mm",
        material_profile="generic",
        tool_diameter=6.0,
        profile_stock_allowance=0.0,
    )
    win._toolpath_plan = SimpleNamespace(
        warnings=["open step skipped"],
        operations=[SimpleNamespace(op_id="cavity_0_rough", strategy="cavity_clearing", feature_type="pocket", metadata={"operation_mode": "roughing"}, motions=[])],
    )

    text = win._build_diagnostics_text()

    assert "raw_candidates: cylinders=2, cones=1, arc_groups=3" in text
    assert "feature_types: pocket=1" in text
    assert "roughing_operations: 1" in text
    assert "piece_projection_bounds: top=8.0, bottom=0.0, depth=8.0" in text
    assert "- open step skipped" in text