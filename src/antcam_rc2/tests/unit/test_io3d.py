"""Unit tests for the 3D importers (STL always, STEP when OCP is present)."""

from __future__ import annotations

import pytest

from antcam_rc2.core.io3d import import_file_3d


@pytest.fixture(scope="module")
def box_stl(tmp_path_factory) -> str:
    import trimesh

    path = tmp_path_factory.mktemp("stl") / "box.stl"
    trimesh.creation.box(extents=[20.0, 10.0, 5.0]).export(str(path))
    return str(path)


def test_import_stl_builds_scene(box_stl: str) -> None:
    scene = import_file_3d(box_stl)
    assert scene.source.format == "stl"
    assert len(scene.bodies) == 1
    assert scene.bodies[0].mesh.face_count >= 12
    assert scene.bodies[0].features
    assert not scene.has_errors()


def test_import_unsupported_extension(tmp_path) -> None:
    path = tmp_path / "part.obj"
    path.write_text("not a real mesh")
    from antcam_rc2.core.errors import UnsupportedFormatError

    with pytest.raises(UnsupportedFormatError):
        import_file_3d(str(path))


def test_import_stl_non_watertight_warns(tmp_path) -> None:
    import trimesh

    path = tmp_path / "open.stl"
    mesh = trimesh.Trimesh(
        vertices=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        faces=[[0, 1, 2]],
    )
    mesh.export(str(path))
    scene = import_file_3d(str(path))
    assert len(scene.bodies) == 1
    assert any("watertight" in warning for warning in scene.warnings)
    assert not scene.has_errors()


def test_import_stl_non_trimesh_is_error(tmp_path, monkeypatch) -> None:
    import trimesh

    path = tmp_path / "x.stl"
    path.write_text("not a mesh")
    monkeypatch.setattr(trimesh, "load", lambda _path: None)
    scene = import_file_3d(str(path))
    assert len(scene.bodies) == 0
    assert scene.has_errors()


def test_import_stl_empty_geometry_skipped(tmp_path, monkeypatch) -> None:
    import trimesh

    path = tmp_path / "x.stl"
    path.write_text("not a mesh")
    empty = trimesh.Trimesh(vertices=[], faces=[])
    scene_obj = trimesh.Scene()
    scene_obj.add_geometry(empty, "empty")
    monkeypatch.setattr(trimesh, "load", lambda _path: scene_obj)
    scene = import_file_3d(str(path))
    assert len(scene.bodies) == 0
    assert scene.has_errors()


def test_import_step_when_ocp_available() -> None:
    pytest.importorskip("OCP")
    import tempfile
    from pathlib import Path

    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    shape = BRepPrimAPI_MakeBox(20.0, 10.0, 5.0).Shape()
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "box.step"
        assert writer.Write(str(path)) == IFSelect_RetDone
        scene = import_file_3d(str(path))
    assert scene.source.format == "step"
    assert len(scene.bodies) == 1
    assert scene.bodies[0].mesh.face_count >= 12
    assert scene.bodies[0].features
    assert not scene.has_errors()
