import logging
import os
from types import SimpleNamespace

import pytest

from antcam import cli
from antcam.importer import Model, import_step, import_stl


def test_import_step():
    path = os.path.join(os.path.dirname(__file__), "data", "cube.step")
    shape = import_step(path)
    assert shape is not None


def test_import_stl():
    path = os.path.join(os.path.dirname(__file__), "data", "cube.stl")
    mesh = import_stl(path, auto_fix=True)
    assert mesh.vertices.shape[1] == 3
    # The STL may not be watertight, but it should still provide faces.
    assert len(mesh.faces) > 0


def test_model_from_stl_stays_mesh_only_when_experimental_flag_is_requested(monkeypatch, caplog):
    fake_mesh = SimpleNamespace(vertices=[[0.0, 0.0, 0.0]], faces=[[0, 1, 2]])

    def _fake_import_stl(path):
        assert path == "dummy.stl"
        return fake_mesh

    monkeypatch.setattr("antcam.importer.import_stl", _fake_import_stl)

    with caplog.at_level(logging.WARNING, logger="antcam"):
        model = Model.from_stl("dummy.stl", convert_to_brep=True)

    assert model.mesh is fake_mesh
    assert model.brep is None
    assert any("Experimental mesh-to-BRep conversion is not enabled" in message for message in caplog.messages)


def test_cli_load_model_routes_stl_to_mesh_only_model(monkeypatch):
    calls = {}
    stl_model = SimpleNamespace(brep=None, mesh=object())

    class _FakeModelFactory:
        @classmethod
        def from_step(cls, path, rx=0.0, ry=0.0, rz=0.0):
            calls["from_step"] = (path, rx, ry, rz)
            return SimpleNamespace(brep=object(), mesh=None)

        @classmethod
        def from_stl(cls, path, convert_to_brep=False):
            calls["from_stl"] = (path, convert_to_brep)
            return stl_model

    monkeypatch.setattr(cli, "Model", _FakeModelFactory)

    model = cli._load_model("dummy.stl", rx=0.0, ry=0.0, rz=0.0)

    assert model is stl_model
    assert calls.get("from_stl") == ("dummy.stl", False)
    assert "from_step" not in calls