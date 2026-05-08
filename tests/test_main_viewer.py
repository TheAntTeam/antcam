import logging
from types import SimpleNamespace

import pytest

from antcam import main_viewer


class _FakeFeatureExtractor:
    def __init__(self, model):
        self.model = model
        self.working_plane_normal = [0.0, 0.0, 1.0]
        self.extract_called = False
        self.vertical_called = False

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
            def from_stl(cls, path, convert_to_brep=True):
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

    assert calls.get("from_stl") == ("tests/data/cube.stl", True)
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
    assert any("Formato file non supportato" in message for message in caplog.messages)


def test_run_feature_viewer_invalid_loaded_model_stops_before_extraction(pipeline_spy, caplog):
    calls, extractors, contours, set_model_factory = pipeline_spy
    set_model_factory(step_model=SimpleNamespace(brep=None, mesh=None))

    with caplog.at_level(logging.ERROR, logger="antcam"):
        main_viewer.run_feature_viewer("tests/data/flange.step")

    assert calls.get("from_step") is not None
    assert not extractors
    assert not contours
    assert "show" not in calls
    assert any("Nessun modello valido caricato" in message for message in caplog.messages)

