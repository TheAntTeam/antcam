import os
import pytest
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
import trimesh
from antcam.importer import import_stl, import_step


def test_import_step():
    path = os.path.join(os.path.dirname(__file__), "data", "cube.step")
    shape = import_step(path)
    assert shape is not None


def test_import_stl():
    path = os.path.join(os.path.dirname(__file__), "data", "cube.stl")
    mesh = import_stl(path, auto_fix=True)
    assert mesh.vertices.shape[1] == 3
    # Non sempre sarà volume, ma almeno deve avere facce
    assert len(mesh.faces) > 0