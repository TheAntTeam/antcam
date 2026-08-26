"""Integration tests for the import pipeline (file -> validated scene)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from antcam_rc2.core.io import import_file

DATA = Path(__file__).parent.parent / "data"

MM_PER_PX = 25.4 / 96.0


def test_import_dxf_scene_validated() -> None:
    scene = import_file(DATA / "mini_square.dxf")
    assert scene.source.format == "dxf"
    assert not scene.diagnostics.has_errors
    box = scene.bounding_box()
    assert box.width == pytest.approx(10.0)
    assert box.height == pytest.approx(10.0)
    # Square made of 4 lines: closed chain
    from antcam_rc2.core.geometry.curves import LineSegment

    assert all(isinstance(e, LineSegment) for e in scene.entities())


def test_import_svg_scene_validated() -> None:
    scene = import_file(DATA / "mini_rect.svg")
    assert scene.source.format == "svg"
    assert not scene.diagnostics.has_errors
    from antcam_rc2.core.geometry.paths import Contour

    contour = next(e for e in scene.entities() if isinstance(e, Contour))
    box = contour.bounding_box()
    # rect 10x20 at (5,5) in px
    assert box.width == pytest.approx(10.0 * MM_PER_PX, rel=1e-6)
    assert box.height == pytest.approx(20.0 * MM_PER_PX, rel=1e-6)


def test_import_polyline_with_arc() -> None:
    scene = import_file(DATA / "mini_polyline_bulge.dxf")
    from antcam_rc2.core.geometry.curves import Arc
    from antcam_rc2.core.geometry.paths import Contour

    contour = scene.entities()[0]
    assert isinstance(contour, Contour)
    assert contour.closed
    assert any(isinstance(seg, Arc) for seg in contour.segments)
    # Square 10x10 + semicircle: known area
    assert contour.area() == pytest.approx(100.0 + math.pi * 25.0 / 2.0, rel=1e-3)


def test_import_groups_transform() -> None:
    scene = import_file(DATA / "mini_groups_transform.svg")
    box = scene.bounding_box()
    assert box.min_x == pytest.approx(50.0, rel=1e-6)
    assert box.width == pytest.approx(20.0 * MM_PER_PX, rel=1e-6)


def test_import_imperial_units_normalized() -> None:
    # Build a tiny imperial DXF on the fly
    tmp = DATA.parent / "imperial.dxf"
    tmp.write_text(
        "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1015\n9\n$INSUNITS\n70\n1\n0\nENDSEC\n"
        "0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n0\n10\n0.0\n20\n0.0\n11\n1.0\n21\n0.0\n"
        "0\nENDSEC\n0\nEOF\n",
        encoding="utf-8",
    )
    try:
        scene = import_file(tmp)
        assert not scene.diagnostics.has_errors
        scene.normalize()
        box = scene.bounding_box()
        assert box.width == pytest.approx(25.4, rel=1e-6)
    finally:
        tmp.unlink(missing_ok=True)
