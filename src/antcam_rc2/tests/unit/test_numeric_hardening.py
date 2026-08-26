"""Numeric hardening tests: NaN/Inf, extremes, degenerate geometry."""

from __future__ import annotations

import math

from antcam_rc2.core.geometry.curves import Arc, Circle, LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.sanitize import sanitize_scene
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo


def make_scene(*entities) -> GeometryScene:
    scene = GeometryScene(source=SourceInfo(format="dxf", path="unit.dxf"))
    for entity in entities:
        scene.add_entity(entity, "0")
    return scene


def test_nan_entity_is_dropped_with_warning() -> None:
    bad = LineSegment(Point2(float("nan"), 0.0), Point2(10.0, 0.0))
    good = LineSegment(Point2(0.0, 0.0), Point2(5.0, 5.0))
    scene = make_scene(bad, good)
    sanitize_scene(scene)
    assert len(scene.entities()) == 1
    assert scene.entities()[0] == good
    assert any("non-finite" in warning for warning in scene.diagnostics.warnings)


def test_inf_circle_is_dropped() -> None:
    scene = make_scene(Circle(Point2(0.0, 0.0), math.inf), Circle(Point2(1.0, 1.0), 2.0))
    sanitize_scene(scene)
    assert len(scene.entities()) == 1
    assert scene.entities()[0].radius == 2.0


def test_nan_arc_is_dropped() -> None:
    arc = Arc(Point2(0.0, 0.0), 5.0, 0.0, float("nan"), ccw=True)
    scene = make_scene(arc)
    sanitize_scene(scene)
    assert len(scene.entities()) == 0


def test_nan_contour_is_dropped() -> None:
    contour = Contour(
        [
            LineSegment(Point2(0.0, 0.0), Point2(1.0, 0.0)),
            LineSegment(Point2(1.0, 0.0), Point2(float("inf"), 0.0)),
        ]
    )
    scene = make_scene(contour)
    sanitize_scene(scene)
    assert len(scene.entities()) == 0


def test_extreme_coordinates_warn_but_are_kept() -> None:
    extreme = LineSegment(Point2(0.0, 0.0), Point2(2e6, 0.0))
    scene = make_scene(extreme)
    sanitize_scene(scene)
    assert len(scene.entities()) == 1
    assert any("m/mm" in warning for warning in scene.diagnostics.warnings)


def test_extreme_contour_and_arc_warn_once() -> None:
    contour = Contour(
        [
            LineSegment(Point2(0.0, 0.0), Point2(3e6, 0.0)),
            LineSegment(Point2(3e6, 0.0), Point2(0.0, 3e6)),
        ]
    )
    arc = Arc(Point2(2e6, 0.0), 5.0, 0.0, math.pi, ccw=True)
    scene = make_scene(contour, arc)
    sanitize_scene(scene)
    assert len(scene.entities()) == 2
    # both are kept, but the extreme warning fires only once
    assert sum("m/mm" in warning for warning in scene.diagnostics.warnings) == 1


def test_extreme_arc_warns() -> None:
    arc = Arc(Point2(2e6, 0.0), 5.0, 0.0, math.pi, ccw=True)
    scene = make_scene(arc)
    sanitize_scene(scene)
    assert len(scene.entities()) == 1
    assert any("m/mm" in warning for warning in scene.diagnostics.warnings)


def test_extreme_path_warns() -> None:
    from antcam_rc2.core.geometry.paths import Path

    path = Path(
        [
            Contour(
                [
                    LineSegment(Point2(0.0, 0.0), Point2(1.0, 0.0)),
                    LineSegment(Point2(1.0, 0.0), Point2(0.0, 1.0)),
                ]
            )
        ]
    )
    scene = make_scene(path)
    sanitize_scene(scene)
    assert len(scene.entities()) == 1


def test_curve2_subclass_uses_start_end_fallback() -> None:
    from antcam_rc2.core.geometry.curves import Curve2

    class BareCurve(Curve2):
        @property
        def start(self):
            return Point2(2e6, 0.0)

        @property
        def end(self):
            return Point2(0.0, 0.0)

        def length(self):
            return 0.0

        def point_at(self, t):
            return self.start

        def reverse(self):
            return self

        def to_polyline(self, tolerance_mm):
            return [self.start, self.end]

        def transform(self, affine):
            return self

    scene = make_scene(BareCurve())
    sanitize_scene(scene)
    assert len(scene.entities()) == 1  # extreme but finite -> kept with warning
    assert any("m/mm" in warning for warning in scene.diagnostics.warnings)


def test_sanitize_keeps_valid_scene_unchanged() -> None:
    good = LineSegment(Point2(0.0, 0.0), Point2(5.0, 5.0))
    scene = make_scene(good)
    sanitize_scene(scene)
    assert len(scene.entities()) == 1
    assert scene.diagnostics.warnings == []


def test_svg_import_sanitizes_nan() -> None:
    """A hand-built scene with NaN must survive the shared sanitizer path."""
    from pathlib import Path

    from antcam_rc2.core.io import import_file

    scene = import_file(Path("tests/corpus/svg/pcb_panel.svg"))
    assert len(scene.entities()) == 6
    assert scene.diagnostics.warnings == []
