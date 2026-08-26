"""Tests for core.io.svg (import via svgpathtools)."""

from __future__ import annotations

from pathlib import Path

import pytest

from antcam_rc2.core.geometry.curves import Arc, LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.io.svg import import_svg
from antcam_rc2.core.units import UnitSystem

DATA = Path(__file__).parent.parent / "data"

MM_PER_PX = 25.4 / 96.0


class TestMiniRect:
    def test_entities(self) -> None:
        scene = import_svg(DATA / "mini_rect.svg")
        # rect -> contour; line -> line segment
        assert len(scene.entities()) == 2
        assert any(isinstance(e, Contour) for e in scene.entities())
        assert any(isinstance(e, LineSegment) for e in scene.entities())

    def test_rect_size_mm(self) -> None:
        scene = import_svg(DATA / "mini_rect.svg")
        contour = next(e for e in scene.entities() if isinstance(e, Contour))
        box = contour.bounding_box()
        assert box.width == pytest.approx(10.0 * MM_PER_PX, rel=1e-6)
        assert box.height == pytest.approx(20.0 * MM_PER_PX, rel=1e-6)

    def test_rect_position(self) -> None:
        scene = import_svg(DATA / "mini_rect.svg")
        contour = next(e for e in scene.entities() if isinstance(e, Contour))
        box = contour.bounding_box()
        assert box.min_x == pytest.approx(5.0 * MM_PER_PX, rel=1e-6)
        assert box.min_y == pytest.approx(5.0 * MM_PER_PX, rel=1e-6)

    def test_units(self) -> None:
        scene = import_svg(DATA / "mini_rect.svg")
        assert scene.units is UnitSystem.METRIC


class TestMiniPath:
    def test_two_paths(self) -> None:
        scene = import_svg(DATA / "mini_path.svg")
        assert len(scene.entities()) == 2

    def test_first_path_has_arc(self) -> None:
        scene = import_svg(DATA / "mini_path.svg")
        contour = next(e for e in scene.entities() if isinstance(e, Contour))
        assert any(isinstance(seg, Arc) for seg in contour.segments)

    def test_bezier_approximated(self) -> None:
        scene = import_svg(DATA / "mini_path.svg")
        assert any("approximated" in w for w in scene.diagnostics.warnings)


class TestMiniGroupsTransform:
    def test_transform_applied(self) -> None:
        scene = import_svg(DATA / "mini_groups_transform.svg")
        contour = scene.entities()[0]
        assert isinstance(contour, Contour)
        box = contour.bounding_box()
        # rect 10x10 at origin, scale(2), translate(50,50) -> 20x20 at (50,50)
        assert box.min_x == pytest.approx(50.0, rel=1e-6)
        assert box.min_y == pytest.approx(50.0, rel=1e-6)
        assert box.width == pytest.approx(20.0 * MM_PER_PX, rel=1e-6)
        assert box.height == pytest.approx(20.0 * MM_PER_PX, rel=1e-6)

    def test_units_warning(self) -> None:
        scene = import_svg(DATA / "mini_groups_transform.svg")
        assert any("px" in w for w in scene.diagnostics.warnings)
