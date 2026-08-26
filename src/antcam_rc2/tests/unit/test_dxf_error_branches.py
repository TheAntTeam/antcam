"""DXF error-branch coverage: skipped types, blocks, splines, units, open polylines."""

from __future__ import annotations

from pathlib import Path

import ezdxf

from antcam_rc2.core.io import import_file


def _write_dxf(tmp_path: Path, name: str, doc) -> Path:
    path = tmp_path / name
    doc.saveas(path)
    return path


def _new_doc(insunits: int = 4):
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = insunits
    return doc


def test_unknown_insunits_warns_and_defaults_metric(tmp_path: Path) -> None:
    doc = _new_doc(insunits=99)
    doc.modelspace().add_line((0, 0), (10, 10))
    scene = import_file(_write_dxf(tmp_path, "units99.dxf", doc))
    assert scene.units.value == "metric"
    assert any("INSUNITS" in warning for warning in scene.diagnostics.warnings)


def test_skipped_types_produce_warnings(tmp_path: Path) -> None:
    doc = _new_doc()
    msp = doc.modelspace()
    msp.add_text("hello")
    msp.add_point((1, 1))
    msp.add_line((0, 0), (5, 5))
    scene = import_file(_write_dxf(tmp_path, "skipped.dxf", doc))
    assert len(scene.entities()) == 1
    assert any("skipped" in warning for warning in scene.diagnostics.warnings)


def test_insert_with_missing_block_is_error() -> None:
    """An INSERT whose block is undefined produces a diagnostics error."""
    from antcam_rc2.core.io.diagnostics import ImportDiagnostics
    from antcam_rc2.core.io.dxf import _collect
    from antcam_rc2.core.io.scene import GeometryScene, SourceInfo

    doc = ezdxf.new("R2010")
    block = doc.blocks.new("GONE")
    block.add_line((0, 0), (1, 1))
    insert = doc.modelspace().add_blockref("GONE", (0, 0))
    insert.dxf.name = "NOPE"  # dangle the reference in-memory

    scene = GeometryScene(source=SourceInfo(format="dxf"))
    diagnostics = ImportDiagnostics()
    _collect(doc.modelspace(), scene, diagnostics, block_stack=())
    assert diagnostics.has_errors
    assert any("Block" in error for error in diagnostics.errors)


def test_insert_with_recursive_block_warns(tmp_path: Path) -> None:
    doc = _new_doc()
    block = doc.blocks.new("REC")
    block.add_blockref("REC", (1, 1))  # self-reference
    doc.modelspace().add_blockref("REC", (0, 0))
    scene = import_file(_write_dxf(tmp_path, "recursive_block.dxf", doc))
    assert any("Recursive" in warning for warning in scene.diagnostics.warnings)


def test_insert_with_empty_block_warns(tmp_path: Path) -> None:
    doc = _new_doc()
    doc.blocks.new("EMPTY")
    doc.modelspace().add_blockref("EMPTY", (0, 0))
    scene = import_file(_write_dxf(tmp_path, "empty_block.dxf", doc))
    assert any("empty" in warning for warning in scene.diagnostics.warnings)


def test_ellipse_is_sampled_to_polyline(tmp_path: Path) -> None:
    doc = _new_doc()
    doc.modelspace().add_ellipse((0, 0), major_axis=(10, 0), ratio=0.5)
    scene = import_file(_write_dxf(tmp_path, "ellipse.dxf", doc))
    from antcam_rc2.core.geometry.paths import Contour

    assert len(scene.entities()) == 1
    assert isinstance(scene.entities()[0], Contour)
    assert len(scene.entities()[0].segments) >= 3


def test_spline_is_sampled_to_polyline(tmp_path: Path) -> None:
    doc = _new_doc()
    doc.modelspace().add_spline_control_frame([(0, 0), (5, 10), (10, 0), (15, -5)])
    scene = import_file(_write_dxf(tmp_path, "spline.dxf", doc))
    from antcam_rc2.core.geometry.paths import Contour

    assert len(scene.entities()) == 1
    assert isinstance(scene.entities()[0], Contour)


def test_open_lwpolyline_is_open_contour(tmp_path: Path) -> None:
    doc = _new_doc()
    doc.modelspace().add_lwpolyline([(0, 0), (5, 0), (5, 5)], format="xyb", close=False)
    scene = import_file(_write_dxf(tmp_path, "open_poly.dxf", doc))
    from antcam_rc2.core.geometry.paths import Contour

    entity = scene.entities()[0]
    assert isinstance(entity, Contour)
    assert not entity.closed


def test_old_style_polyline(tmp_path: Path) -> None:
    doc = _new_doc()
    polyline = doc.modelspace().add_polyline3d([(0, 0, 0), (5, 0, 0), (5, 5, 0)])
    polyline.close(True)
    scene = import_file(_write_dxf(tmp_path, "poly3d.dxf", doc))
    assert len(scene.entities()) == 1


def test_degenerate_bulge_is_line(tmp_path: Path) -> None:
    """A LWPOLYLINE bulge with a zero-length chord degrades to a line (no crash)."""
    doc = _new_doc()
    doc.modelspace().add_lwpolyline([(0, 0, 1.0), (0, 0, 0.0)], format="xyb", close=False)
    scene = import_file(_write_dxf(tmp_path, "degenerate.dxf", doc))
    assert len(scene.entities()) >= 1
