from pathlib import Path

import pytest

from antcam.importer import Model

TEST_DATA = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "data"


@pytest.mark.parametrize("step_file", sorted(TEST_DATA.glob("*.step")))
def test_import_step(step_file: Path):
    model = Model.from_step(step_file)
    assert not model.is_mesh_only
    assert model.face_count() > 0
    assert not model.shape.IsNull()


@pytest.mark.parametrize("stl_file", sorted(TEST_DATA.glob("*.stl")))
def test_import_stl(stl_file: Path):
    model = Model.from_stl(stl_file)
    assert model.is_mesh_only
    assert model.mesh.is_watertight is not None


def test_step_rotation():
    ref = TEST_DATA / "flange.step"
    model = Model.from_step(ref, rx=90)
    assert model.rotation == (90, 0, 0)


def test_to_dict():
    ref = TEST_DATA / "cube.step"
    model = Model.from_step(ref)
    d = model.to_dict()
    assert d["source"].endswith("cube.step")
    assert d["face_count"] > 0
    assert d["mesh_only"] is False
