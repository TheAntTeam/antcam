"""SVG error-branch coverage: malformed paths, shapes, transforms, units."""

from __future__ import annotations

from pathlib import Path

import pytest

from antcam_rc2.core.geometry.curves import Circle, LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.io import import_file


def _svg(tmp_path: Path, name: str, body: str, attrs: str = "") -> Path:
    path = tmp_path / name
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}>{body}</svg>',
        encoding="utf-8",
    )
    return path


def _scene(tmp_path: Path, name: str, body: str, attrs: str = "") -> object:
    return import_file(_svg(tmp_path, name, body, attrs))


def test_path_parse_error_is_diagnosed(tmp_path: Path) -> None:
    scene = _scene(tmp_path, "bad_path.svg", '<path d="not a path"/>')
    assert scene.diagnostics.has_errors
    assert any("Failed to parse SVG path" in error for error in scene.diagnostics.errors)


def test_elliptical_arc_is_sampled_with_warning(tmp_path: Path) -> None:
    # A rx != ry arc: A x1 y1 x2 y2 fA fS rx ry x y
    scene = _scene(
        tmp_path,
        "ell_arc.svg",
        '<path d="M 0 0 A 10 20 0 0 1 20 0 Z"/>',
    )
    assert len(scene.entities()) == 1
    assert any("approximated" in warning for warning in scene.diagnostics.warnings)


def test_circular_arc_becomes_native_arc(tmp_path: Path) -> None:
    scene = _scene(
        tmp_path,
        "circ_arc.svg",
        '<path d="M 0 10 L 0 0 A 10 10 0 0 1 20 0 L 20 10 Z"/>',
    )
    from antcam_rc2.core.geometry.curves import Arc

    assert len(scene.entities()) == 1
    assert any(isinstance(seg, Arc) for seg in scene.entities()[0].segments)


def test_open_path_with_fewer_than_3_segments_is_skipped(tmp_path: Path) -> None:
    scene = _scene(tmp_path, "open.svg", '<path d="M 0 0 L 10 0"/>')
    assert len(scene.entities()) == 0


def test_bezier_is_approximated(tmp_path: Path) -> None:
    scene = _scene(tmp_path, "bez.svg", '<path d="M 0 0 C 5 10 15 10 20 0 Z"/>')
    assert len(scene.entities()) == 1
    assert any("approximated" in warning for warning in scene.diagnostics.warnings)


def test_ellipse_approximated(tmp_path: Path) -> None:
    scene = _scene(tmp_path, "ell.svg", '<ellipse cx="10" cy="10" rx="8" ry="4"/>')
    assert len(scene.entities()) == 1
    assert isinstance(scene.entities()[0], Contour)
    assert len(scene.entities()[0].segments) >= 3
    assert any("Ellipse" in warning for warning in scene.diagnostics.warnings)


def test_rect_invalid_dimensions_is_error(tmp_path: Path) -> None:
    scene = _scene(tmp_path, "bad_rect.svg", '<rect x="a"/>')
    assert scene.diagnostics.has_errors


def test_circle_invalid_radius_is_error(tmp_path: Path) -> None:
    scene = _scene(tmp_path, "bad_circle.svg", '<circle r="boom"/>')
    assert scene.diagnostics.has_errors


def test_line_invalid_is_error(tmp_path: Path) -> None:
    scene = _scene(tmp_path, "bad_line.svg", '<line x1="x"/>')
    assert scene.diagnostics.has_errors


def test_polyline_success_and_empty_warning(tmp_path: Path) -> None:
    ok = _scene(tmp_path, "polyline.svg", '<polyline points="0,0 10,0 10,10 0,10"/>')
    assert len(ok.entities()) == 1
    empty = _scene(tmp_path, "polyline_empty.svg", "<polyline/>")
    assert len(empty.entities()) == 0
    assert any("Empty" in warning for warning in empty.diagnostics.warnings)


def test_polygon_success_and_degenerate_warning(tmp_path: Path) -> None:
    ok = _scene(tmp_path, "polygon.svg", '<polygon points="0,0 10,0 10,10 0,10"/>')
    assert len(ok.entities()) == 1
    assert isinstance(ok.entities()[0], Contour)
    assert ok.entities()[0].closed
    bad = _scene(tmp_path, "polygon_bad.svg", '<polygon points="0,0 10,0"/>')
    assert len(bad.entities()) == 0
    assert any("Degenerate" in warning for warning in bad.diagnostics.warnings)


def test_points_with_invalid_values_stops_parsing(tmp_path: Path) -> None:
    scene = _scene(tmp_path, "bad_points.svg", '<polygon points="0,0 10,0 oops 20,20"/>')
    # Only (0,0) parsed -> < 3 points -> degenerate warning, no crash.
    assert len(scene.entities()) == 0
    assert any("Degenerate" in warning for warning in scene.diagnostics.warnings)


def test_combined_transform_translate_scale_rotate(tmp_path: Path) -> None:
    scene = _scene(
        tmp_path,
        "transform.svg",
        '<line x1="0" y1="0" x2="10" y2="0" transform="translate(5 5) scale(2) rotate(90)"/>',
    )
    entity = scene.entities()[0]
    assert isinstance(entity, LineSegment)
    mm = 25.4 / 96.0  # px -> mm base scale (no viewBox)
    # Transform composes after the base scale: translate(5 5) moves in mm space,
    # scale(2) then rotates the (already scaled) shape.
    assert entity.start.x == pytest.approx(5.0)
    assert entity.start.y == pytest.approx(5.0)
    assert entity.end.x == pytest.approx(5.0)
    assert entity.end.y == pytest.approx(5.0 + 20.0 * mm)


def test_transform_with_bad_number_is_skipped(tmp_path: Path) -> None:
    scene = _scene(
        tmp_path,
        "bad_transform.svg",
        '<line x1="0" y1="0" x2="10" y2="0" transform="translate(abc) scale(2)"/>',
    )
    # translate(abc) is skipped, scale(2) applies (then px->mm base scale)
    entity = scene.entities()[0]
    assert entity.end.x == pytest.approx(20.0 * 25.4 / 96.0)


def test_units_cm_with_viewbox(tmp_path: Path) -> None:
    scene = _scene(
        tmp_path,
        "cm.svg",
        '<rect width="50" height="50"/>',
        'width="10cm" viewBox="0 0 50 50"',
    )
    assert scene.units.value == "metric"
    entity = scene.entities()[0]
    # 50 user units = 100 mm -> scale 2.0
    assert entity.segments[0].end.x == pytest.approx(100.0)


def test_units_in_with_viewbox(tmp_path: Path) -> None:
    scene = _scene(
        tmp_path,
        "in.svg",
        '<rect width="50" height="50"/>',
        'width="1in" viewBox="0 0 50 50"',
    )
    assert scene.units.value == "imperial"
    entity = scene.entities()[0]
    assert entity.segments[0].end.x == pytest.approx(25.4)


def test_units_pt_with_viewbox(tmp_path: Path) -> None:
    scene = _scene(
        tmp_path,
        "pt.svg",
        '<rect width="50" height="50"/>',
        'width="72pt" viewBox="0 0 50 50"',
    )
    entity = scene.entities()[0]
    # 72 pt = 25.4 mm over 50 user units
    assert entity.segments[0].end.x == pytest.approx(25.4)


def test_invalid_width_with_viewbox_falls_back_to_px(tmp_path: Path) -> None:
    scene = _scene(
        tmp_path,
        "bad_width.svg",
        '<rect width="50" height="50"/>',
        'width="abc" viewBox="0 0 50 50"',
    )
    assert len(scene.entities()) == 1  # px fallback, still imports
    assert any("not declared" in warning for warning in scene.diagnostics.warnings)


def test_viewbox_with_invalid_numbers_falls_back(tmp_path: Path) -> None:
    scene = _scene(
        tmp_path,
        "bad_viewbox.svg",
        '<rect width="50" height="50"/>',
        'width="100mm" viewBox="oops"',
    )
    assert len(scene.entities()) == 1


def test_missing_units_and_viewbox_warns(tmp_path: Path) -> None:
    scene = _scene(tmp_path, "nude.svg", '<rect width="50" height="50"/>')
    assert any("not declared" in warning for warning in scene.diagnostics.warnings)


def test_defs_are_skipped(tmp_path: Path) -> None:
    scene = _scene(
        tmp_path,
        "defs.svg",
        '<defs><line x1="0" y1="0" x2="99" y2="99"/></defs><line x1="0" y1="0" x2="5" y2="5"/>',
    )
    assert len(scene.entities()) == 1


def test_nested_group_layers_from_inkscape_label(tmp_path: Path) -> None:
    scene = _scene(
        tmp_path,
        "layers.svg",
        (
            '<g inkscape:label="top"><circle cx="5" cy="5" r="2"/></g>'
            '<g id="mid"><line x1="0" y1="0" x2="5" y2="5"/></g>'
            '<line x1="0" y1="0" x2="1" y2="1"/>'
        ),
        'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"',
    )
    assert [layer.name for layer in scene.layers] == ["top", "mid", "0"]
    assert len(scene.entities()) == 3
    assert isinstance(scene.entities()[0], Circle)


def test_empty_path_d_skipped(tmp_path: Path) -> None:
    scene = _scene(tmp_path, "empty_d.svg", '<path d=""/>')
    assert len(scene.entities()) == 0
