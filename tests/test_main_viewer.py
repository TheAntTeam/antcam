import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from antcam import main_viewer
from antcam.planner_config import PlannerRuntimeConfig


class _FakeFeatureExtractor:
    def __init__(self, model):
        self.model = model
        self.working_plane_normal = [0.0, 0.0, 1.0]
        self.extract_called = False
        self.vertical_called = False
        self.diagnostics = {
            "raw_candidates": {"cylinders": 1, "cones": 0, "arc_groups": 1},
            "feature_count": 1,
        }

    def extract(self):
        self.extract_called = True
        return [SimpleNamespace(type="mock_feature", props={}, geometry=None)]

    def find_vertical_faces_with_xy_arcs(self):
        self.vertical_called = True
        return [{"id": 1}]


class _FakeContourExtractor:
    def __init__(self, model, working_plane_normal, features):
        self.model = model
        self.working_plane_normal = working_plane_normal
        self.features = features
        self.extract_called = False
        self.extract_perimeter_called_with = None

    def extract(self):
        self.extract_called = True
        return "shadow"

    def extract_perimeter(self, contour_shadow):
        self.extract_perimeter_called_with = contour_shadow
        return ["wire_1"]


@pytest.fixture
def pipeline_spy(monkeypatch):
    calls = {}

    def _set_model_factory(step_model=None, stl_model=None):
        class _FakeModelFactory:
            @classmethod
            def from_step(cls, path, rx=0.0, ry=0.0, rz=0.0):
                calls["from_step"] = (path, rx, ry, rz)
                return step_model

            @classmethod
            def from_stl(cls, path, convert_to_brep=False):
                calls["from_stl"] = (path, convert_to_brep)
                return stl_model

        monkeypatch.setattr(main_viewer, "Model", _FakeModelFactory)

    created_extractors = []
    created_contours = []

    def _feature_factory(model):
        extractor = _FakeFeatureExtractor(model)
        created_extractors.append(extractor)
        return extractor

    def _contour_factory(model, working_plane_normal, features):
        contour = _FakeContourExtractor(model, working_plane_normal, features)
        created_contours.append(contour)
        return contour

    def _show_model_with_features(model, features, **kwargs):
        calls["show"] = {
            "model": model,
            "features": features,
            "kwargs": kwargs,
        }

    monkeypatch.setattr(main_viewer, "FeatureExtractor", _feature_factory)
    monkeypatch.setattr(main_viewer, "ContourExtractor", _contour_factory)
    monkeypatch.setattr(main_viewer, "show_model_with_features", _show_model_with_features)

    return calls, created_extractors, created_contours, _set_model_factory


def test_run_feature_viewer_step_route(pipeline_spy):
    calls, extractors, contours, set_model_factory = pipeline_spy
    step_model = SimpleNamespace(brep=object(), mesh=None)
    set_model_factory(step_model=step_model)

    main_viewer.run_feature_viewer("tests/data/flange.step", rx=10.0, ry=20.0, rz=30.0)

    assert calls.get("from_step") == ("tests/data/flange.step", 10.0, 20.0, 30.0)
    assert "from_stl" not in calls
    assert extractors and extractors[0].extract_called and extractors[0].vertical_called
    assert contours and contours[0].extract_called
    assert contours[0].extract_perimeter_called_with == "shadow"

    shown = calls.get("show")
    assert shown is not None
    assert shown["model"] is step_model
    assert shown["kwargs"]["contour_shadow"] == "shadow"
    assert shown["kwargs"]["perimeter"] == ["wire_1"]
    assert shown["kwargs"]["working_plane_normal"] == (0.0, 0.0, 1.0)
    assert shown["kwargs"]["toolpath_plan"] is not None


def test_build_viewer_analysis_returns_shared_bundle(pipeline_spy):
    calls, extractors, contours, set_model_factory = pipeline_spy
    step_model = SimpleNamespace(brep=object(), mesh=None)
    set_model_factory(step_model=step_model)

    analysis = main_viewer.build_viewer_analysis("tests/data/flange.step", rx=1.0, ry=2.0, rz=3.0)

    assert calls.get("from_step") == ("tests/data/flange.step", 1.0, 2.0, 3.0)
    assert extractors and extractors[0].extract_called and extractors[0].vertical_called
    assert contours and contours[0].extract_called
    assert analysis is not None
    assert analysis.model is step_model
    assert analysis.working_plane_normal == (0.0, 0.0, 1.0)
    assert analysis.contour_shadow == "shadow"
    assert analysis.perimeter == ["wire_1"]
    assert analysis.vertical_arc_groups == [{"id": 1}]
    assert analysis.toolpath_plan is not None


def test_build_viewer_analysis_real_flange_includes_cavity_operations_without_profile_fallback():
    analysis = main_viewer.build_viewer_analysis(
        "tests/data/flange.step",
        planner_config=PlannerRuntimeConfig(max_stepdown=4.0),
    )

    assert analysis is not None
    assert analysis.perimeter
    assert analysis.toolpath_plan is not None
    assert analysis.toolpath_plan.operations
    cavity_operations = [
        operation
        for operation in analysis.toolpath_plan.operations
        if operation.strategy == "cavity_clearing"
    ]
    profile_operations = [
        operation
        for operation in analysis.toolpath_plan.operations
        if operation.strategy == "2p5d_profile"
    ]
    assert cavity_operations
    assert any(operation.feature_type == "pocket" for operation in cavity_operations)
    assert profile_operations == []


def test_build_piece_roughing_analysis_skips_semantic_features_and_accepts_pathlike_input(pipeline_spy):
    calls, _extractors, contours, set_model_factory = pipeline_spy
    step_model = SimpleNamespace(brep=object(), mesh=None)
    set_model_factory(step_model=step_model)

    def _compute_model_projection_bounds(model, working_plane_normal):
        calls["piece_projection_bounds_args"] = (model, tuple(float(v) for v in working_plane_normal))
        return (-5.0, 2.0)

    main_viewer._compute_model_projection_bounds = _compute_model_projection_bounds

    class _FakePlanner:
        def generate(
            self,
            features,
            perimeter_wires=None,
            piece_top_projection=None,
            piece_bottom_projection=None,
        ):
            calls["piece_planner_generate"] = {
                "features": features,
                "perimeter_wires": perimeter_wires,
                "piece_top_projection": piece_top_projection,
                "piece_bottom_projection": piece_bottom_projection,
            }
            return SimpleNamespace(operations=[], warnings=[])

    class _FakePlannerConfig:
        piece_roughing_only = True

        def build_planner(self, working_plane_normal):
            calls["piece_planner_normal"] = tuple(float(v) for v in working_plane_normal)
            return _FakePlanner()

    analysis = main_viewer.build_piece_roughing_analysis(
        Path("tests/data/flange.step"),
        planner_config=_FakePlannerConfig(),
    )

    assert calls.get("from_step") == (str(Path("tests/data/flange.step")), 0.0, 0.0, 0.0)
    assert contours and contours[0].features == []
    assert calls["piece_planner_generate"]["features"] == []
    assert calls["piece_planner_generate"]["perimeter_wires"] == ["wire_1"]
    assert calls["piece_planner_generate"]["piece_top_projection"] == 2.0
    assert calls["piece_planner_generate"]["piece_bottom_projection"] == -5.0
    assert analysis is not None
    assert analysis.features == []
    assert analysis.diagnostics["skip_reason"] == "piece_roughing_only"
    assert analysis.diagnostics["piece_projection_bounds"] == {"bottom": -5.0, "top": 2.0, "depth": 7.0}


@pytest.mark.parametrize("step_path", ["tests/data/flange.stp", "tests/data/FLANGE.STP"])
def test_run_feature_viewer_stp_route_uses_step_loader(pipeline_spy, step_path):
    calls, extractors, contours, set_model_factory = pipeline_spy
    step_model = SimpleNamespace(brep=object(), mesh=None)
    set_model_factory(step_model=step_model)

    main_viewer.run_feature_viewer(step_path, rx=5.0, ry=6.0, rz=7.0)

    assert calls.get("from_step") == (step_path, 5.0, 6.0, 7.0)
    assert "from_stl" not in calls
    assert extractors and extractors[0].extract_called
    assert contours and contours[0].extract_called
    assert calls.get("show") is not None


def test_run_feature_viewer_stl_route(pipeline_spy):
    calls, extractors, contours, set_model_factory = pipeline_spy
    stl_model = SimpleNamespace(brep=None, mesh=object())
    set_model_factory(stl_model=stl_model)

    main_viewer.run_feature_viewer("tests/data/cube.stl")

    assert calls.get("from_stl") == ("tests/data/cube.stl", False)
    assert "from_step" not in calls
    assert extractors and extractors[0].extract_called and extractors[0].vertical_called
    assert contours and contours[0].extract_called
    assert calls.get("show") is not None


def test_run_feature_viewer_unsupported_extension_logs_error_and_stops(pipeline_spy, caplog):
    calls, _extractors, _contours, set_model_factory = pipeline_spy
    set_model_factory(step_model=SimpleNamespace(brep=object(), mesh=None), stl_model=SimpleNamespace(brep=None, mesh=object()))

    with caplog.at_level(logging.ERROR, logger="antcam"):
        main_viewer.run_feature_viewer("tests/data/part.iges")

    assert "from_step" not in calls and "from_stl" not in calls
    assert "show" not in calls
    assert any("Unsupported file format" in message for message in caplog.messages)


def test_run_feature_viewer_invalid_loaded_model_stops_before_extraction(pipeline_spy, caplog):
    calls, extractors, contours, set_model_factory = pipeline_spy
    set_model_factory(step_model=SimpleNamespace(brep=None, mesh=None))

    with caplog.at_level(logging.ERROR, logger="antcam"):
        main_viewer.run_feature_viewer("tests/data/flange.step")

    assert calls.get("from_step") is not None
    assert not extractors
    assert not contours
    assert "show" not in calls
    assert any("No valid model geometry was loaded" in message for message in caplog.messages)


def test_run_feature_viewer_logs_recognition_diagnostics(pipeline_spy, caplog):
    calls, _extractors, _contours, set_model_factory = pipeline_spy
    set_model_factory(step_model=SimpleNamespace(brep=object(), mesh=None))

    with caplog.at_level(logging.INFO, logger="antcam"):
        main_viewer.run_feature_viewer("tests/data/flange.step")

    assert calls.get("from_step") is not None
    assert any("Recognition diagnostics:" in message for message in caplog.messages)


def test_run_feature_viewer_uses_custom_planner_config(pipeline_spy, monkeypatch):
    calls, _extractors, _contours, set_model_factory = pipeline_spy
    set_model_factory(step_model=SimpleNamespace(brep=object(), mesh=None))
    planner_config = PlannerRuntimeConfig(
        parameter_mode="automatic",
        tool_diameter=8.0,
        drill_tool_id="drill_8mm",
        mill_tool_id="endmill_6mm",
    )

    def _build_planner(self, working_plane_normal):
        calls["planner_config"] = self
        calls["planner_normal"] = tuple(float(v) for v in working_plane_normal)

        class _FakePlanner:
            def generate(self, features, perimeter_wires=None):
                calls["planner_generate"] = {
                    "features": features,
                    "perimeter_wires": perimeter_wires,
                }
                return SimpleNamespace(operations=[], warnings=[])

        return _FakePlanner()

    monkeypatch.setattr(PlannerRuntimeConfig, "build_planner", _build_planner)

    main_viewer.run_feature_viewer("tests/data/flange.step", planner_config=planner_config)

    assert calls["planner_config"] is planner_config
    assert calls["planner_normal"] == (0.0, 0.0, 1.0)
    assert calls["planner_generate"]["perimeter_wires"] == ["wire_1"]
    assert calls["show"]["kwargs"]["toolpath_plan"].warnings == []


def test_run_feature_viewer_uses_custom_viewer_callable(pipeline_spy):
    calls, _extractors, _contours, set_model_factory = pipeline_spy
    set_model_factory(step_model=SimpleNamespace(brep=object(), mesh=None))

    custom_calls = {}

    def _custom_viewer(model, features, **kwargs):
        custom_calls["model"] = model
        custom_calls["features"] = features
        custom_calls["kwargs"] = kwargs

    main_viewer.run_feature_viewer("tests/data/flange.step", show_viewer=_custom_viewer)

    assert "show" not in calls
    assert custom_calls["model"].brep is not None
    assert custom_calls["kwargs"]["toolpath_plan"] is not None
    assert custom_calls["kwargs"]["working_plane_normal"] == (0.0, 0.0, 1.0)


def test_run_piece_viewer_uses_piece_only_viewer_config(pipeline_spy):
    calls, extractors, contours, set_model_factory = pipeline_spy
    set_model_factory(step_model=SimpleNamespace(brep=object(), mesh=None))

    main_viewer.run_piece_viewer("tests/data/flange.step")

    assert calls.get("from_step") == ("tests/data/flange.step", 0.0, 0.0, 0.0)
    assert not extractors
    assert contours and contours[0].extract_called
    assert contours[0].extract_perimeter_called_with == "shadow"
    shown = calls.get("show")
    assert shown is not None
    assert shown["features"] == []
    assert shown["kwargs"]["contour_shadow"] == "shadow"
    assert shown["kwargs"]["perimeter"] == ["wire_1"]
    assert shown["kwargs"]["vertical_arc_groups"] == []
    assert shown["kwargs"]["toolpath_plan"] is None
    assert shown["kwargs"]["enable_face_selection"] is False
    assert shown["kwargs"]["show_toolpath_legend"] is False
    assert shown["kwargs"]["show_model_edges"] is True


def test_run_piece_viewer_uses_custom_viewer_callable(pipeline_spy):
    _calls, extractors, contours, set_model_factory = pipeline_spy
    set_model_factory(step_model=SimpleNamespace(brep=object(), mesh=None))

    custom_calls = {}

    def _custom_viewer(model, features, **kwargs):
        custom_calls["model"] = model
        custom_calls["features"] = features
        custom_calls["kwargs"] = kwargs

    main_viewer.run_piece_viewer("tests/data/flange.step", show_viewer=_custom_viewer)

    assert not extractors
    assert contours and contours[0].extract_called
    assert custom_calls["model"].brep is not None
    assert custom_calls["features"] == []
    assert custom_calls["kwargs"]["contour_shadow"] == "shadow"
    assert custom_calls["kwargs"]["perimeter"] == ["wire_1"]
    assert custom_calls["kwargs"]["enable_face_selection"] is False
    assert custom_calls["kwargs"]["show_toolpath_legend"] is False
    assert custom_calls["kwargs"]["show_model_edges"] is True


def test_build_piece_toolpath_preview_analysis_builds_profile_preview_for_outer_loop_and_holes(pipeline_spy, monkeypatch):
    calls, _extractors, contours, set_model_factory = pipeline_spy
    set_model_factory(step_model=SimpleNamespace(brep=object(), mesh=None))
    monkeypatch.setattr(main_viewer, "_compute_model_projection_bounds", lambda model, normal: (-4.0, 2.0))

    outer_loop = [
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
        (10.0, 10.0, 0.0),
        (0.0, 10.0, 0.0),
    ]
    inner_loop = [
        (3.0, 3.0, 0.0),
        (4.0, 3.0, 0.0),
        (4.0, 4.0, 0.0),
        (3.0, 4.0, 0.0),
    ]

    class _FakeContourPlanner:
        piece_roughing_only = False

        def wires_to_polylines(self, wires):
            calls["contour_wires"] = wires
            return [outer_loop, inner_loop]

        def _normalize_planar_loops(self, loops):
            return list(loops)

        def _point_at_projection(self, point, projection):
            return (float(point[0]), float(point[1]), float(projection))

        def _build_profile_operation(
            self,
            op_id,
            points,
            safe_projection,
            top_projection,
            operation_mode,
            geometry_source,
            stock_to_leave=0.0,
            prefer_smaller_area=False,
                **kwargs,
        ):
            calls.setdefault("contour_profiles", []).append({
                "op_id": op_id,
                "points": points,
                "safe_projection": safe_projection,
                "top_projection": top_projection,
                "operation_mode": operation_mode,
                "geometry_source": geometry_source,
                "stock_to_leave": stock_to_leave,
                "prefer_smaller_area": prefer_smaller_area,
                    "kwargs": kwargs,
            })
            return SimpleNamespace(strategy="2p5d_profile", op_id=op_id, metadata={"operation_mode": operation_mode}, motions=[])

    class _FakePieceRoughPlanner:
        piece_roughing_only = True

        def generate(
            self,
            features,
            perimeter_wires=None,
            piece_top_projection=None,
            piece_bottom_projection=None,
        ):
            calls["piece_preview_roughing_generate"] = {
                "features": features,
                "perimeter_wires": perimeter_wires,
                "piece_top_projection": piece_top_projection,
                "piece_bottom_projection": piece_bottom_projection,
            }
            return SimpleNamespace(
                safe_projection=7.0,
                operations=[
                    SimpleNamespace(
                        strategy="cavity_clearing",
                        op_id="piece_rough_0",
                        metadata={"operation_mode": "roughing"},
                        motions=[],
                    )
                ],
                warnings=["Piece rough only mode enabled"],
            )

    def _build_planner(self, working_plane_normal):
        calls.setdefault("planner_normals", []).append(tuple(float(v) for v in working_plane_normal))
        if getattr(self, "piece_roughing_only", False):
            return _FakePieceRoughPlanner()
        return _FakeContourPlanner()

    monkeypatch.setattr(PlannerRuntimeConfig, "build_planner", _build_planner)

    analysis = main_viewer.build_piece_toolpath_preview_analysis(
        "tests/data/flange.step",
        planner_config=PlannerRuntimeConfig(),
    )

    assert contours and contours[0].extract_called
    assert contours[0].extract_perimeter_called_with == "shadow"
    assert analysis is not None
    assert [operation.strategy for operation in analysis.toolpath_plan.operations] == [
        "cavity_clearing",
        "2p5d_profile",
        "2p5d_profile",
    ]
    assert calls["piece_preview_roughing_generate"]["features"] == []
    assert calls["piece_preview_roughing_generate"]["perimeter_wires"] == ["wire_1"]
    assert calls["piece_preview_roughing_generate"]["piece_top_projection"] == 2.0
    assert calls["piece_preview_roughing_generate"]["piece_bottom_projection"] == -4.0
    assert [profile["op_id"] for profile in calls["contour_profiles"]] == ["profile_0", "profile_1"]
    assert [profile["prefer_smaller_area"] for profile in calls["contour_profiles"]] == [False, True]
    assert all(profile["top_projection"] == 2.0 for profile in calls["contour_profiles"])
    assert all(profile["safe_projection"] == 7.0 for profile in calls["contour_profiles"])
    assert all(profile["geometry_source"] == "perimeter_occ_wire_top_projection" for profile in calls["contour_profiles"])
    assert calls["contour_profiles"][0]["points"] == [
        (0.0, 0.0, 2.0),
        (10.0, 0.0, 2.0),
        (10.0, 10.0, 2.0),
        (0.0, 10.0, 2.0),
    ]
    assert calls["contour_profiles"][1]["points"] == [
        (3.0, 3.0, 2.0),
        (4.0, 3.0, 2.0),
        (4.0, 4.0, 2.0),
        (3.0, 4.0, 2.0),
    ]
    assert analysis.diagnostics["toolpath_preview"]["roughing_operations"] == 1
    assert analysis.diagnostics["toolpath_preview"]["contour_operations"] == 2
    assert analysis.diagnostics["toolpath_preview"]["contour_preview_loops"] == 2


def test_run_piece_toolpath_preview_uses_custom_viewer_callable(pipeline_spy, monkeypatch):
    _calls, _extractors, _contours, set_model_factory = pipeline_spy
    set_model_factory(step_model=SimpleNamespace(brep=object(), mesh=None))

    fake_analysis = SimpleNamespace(
        model=SimpleNamespace(brep=object(), mesh=None),
        features=[],
        diagnostics={"toolpath_preview": {"roughing_operations": 1, "contour_operations": 1}},
        working_plane_normal=(0.0, 0.0, 1.0),
        contour_shadow="shadow",
        perimeter=["wire_1"],
        vertical_arc_groups=[],
        toolpath_plan=SimpleNamespace(operations=[], warnings=[]),
        planner_config=PlannerRuntimeConfig(),
    )
    monkeypatch.setattr(main_viewer, "build_piece_toolpath_preview_analysis", lambda *args, **kwargs: fake_analysis)

    custom_calls = {}

    def _custom_viewer(model, features, **kwargs):
        custom_calls["model"] = model
        custom_calls["features"] = features
        custom_calls["kwargs"] = kwargs

    main_viewer.run_piece_toolpath_preview("tests/data/flange.step", show_viewer=_custom_viewer)

    assert custom_calls["features"] == []
    assert custom_calls["kwargs"]["toolpath_plan"].warnings == []
    assert custom_calls["kwargs"]["diagnostics"]["toolpath_preview"]["contour_operations"] == 1


def test_run_feature_viewer_reuses_cached_arc_groups(monkeypatch):
    calls = {}

    class _FakeModelFactory:
        @classmethod
        def from_step(cls, path, rx=0.0, ry=0.0, rz=0.0):
            calls["from_step"] = (path, rx, ry, rz)
            return SimpleNamespace(brep=object(), mesh=None)

    class _CachedArcExtractor:
        def __init__(self, model):
            self.model = model
            self.working_plane_normal = [0.0, 0.0, 1.0]
            self.diagnostics = {}
            self._arc_groups = [{"id": "cached"}]

        def extract(self):
            return [SimpleNamespace(type="mock_feature", props={}, geometry=None)]

        def find_vertical_faces_with_xy_arcs(self):
            raise AssertionError("find_vertical_faces_with_xy_arcs should not be called when cached arc groups exist")

    class _FakeContourExtractor:
        def __init__(self, model, working_plane_normal, features):
            self.model = model
            self.working_plane_normal = working_plane_normal
            self.features = features

        def extract(self):
            return "shadow"

        def extract_perimeter(self, contour_shadow):
            return ["wire_1"]

    def _show_model_with_features(model, features, **kwargs):
        calls["show"] = kwargs

    monkeypatch.setattr(main_viewer, "Model", _FakeModelFactory)
    monkeypatch.setattr(main_viewer, "FeatureExtractor", _CachedArcExtractor)
    monkeypatch.setattr(main_viewer, "ContourExtractor", _FakeContourExtractor)
    monkeypatch.setattr(main_viewer, "show_model_with_features", _show_model_with_features)

    main_viewer.run_feature_viewer("tests/data/flange.step")

    assert calls.get("from_step") is not None
    assert calls["show"]["vertical_arc_groups"] == [{"id": "cached"}]


def test_run_feature_viewer_stops_when_gui_dependencies_are_unavailable(pipeline_spy, monkeypatch, caplog):
    calls, extractors, contours, set_model_factory = pipeline_spy
    set_model_factory(step_model=SimpleNamespace(brep=object(), mesh=None))
    monkeypatch.setattr(main_viewer, "show_model_with_features", None)

    with caplog.at_level(logging.ERROR, logger="antcam"):
        main_viewer.run_feature_viewer("tests/data/flange.step")

    assert calls.get("from_step") is not None
    assert not extractors
    assert not contours
    assert "show" not in calls
    assert any("Viewer dependencies are unavailable" in message for message in caplog.messages)

