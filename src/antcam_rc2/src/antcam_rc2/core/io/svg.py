"""SVG importer using svgpathtools.

Maps SVG elements to native geometry primitives:

- ``<path d>`` -> :class:`LineSegment` / :class:`Arc` directly; bezier
  segments sampled to polylines
- ``<line>``, ``<polyline>``, ``<polygon>`` -> lines / contours
- ``<rect>``, ``<circle>``, ``<ellipse>`` -> contours / circles
- ``<g>`` groups: nested transforms accumulated left-to-right

Units are resolved from ``width``/``height``/``viewBox`` (pixels are treated as
96 dpi, i.e. 25.4/96 mm each).  Missing viewBox defaults to metric with a
warning.  All approximations (bezier, ellipse) are recorded as warnings.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from svgpathtools import Arc as SvgArc
from svgpathtools import CubicBezier, Line, QuadraticBezier, parse_path
from svgpathtools import Line as SvgLine

from antcam_rc2.core.geometry.curves import Arc, Circle, Curve2, LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.geometry.sampling import approximate_to_polyline
from antcam_rc2.core.geometry.transform import Affine2D, apply
from antcam_rc2.core.io.diagnostics import ImportDiagnostics
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.units import UnitSystem

__all__ = ["import_svg"]

SVG_NS = "{http://www.w3.org/2000/svg}"
INKSCAPE_NS = "{http://www.inkscape.org/namespaces/inkscape}"
INKSCAPE_LABEL = f"{INKSCAPE_NS}label"


def import_svg(path: str | Path) -> GeometryScene:
    """Import an SVG file into a :class:`GeometryScene`."""
    file_path = Path(path)
    diagnostics = ImportDiagnostics()
    root = ET.parse(file_path).getroot()

    units, scale = _resolve_units(root, diagnostics)

    scene = GeometryScene(
        source=SourceInfo(
            format="svg",
            path=str(file_path),
            original_units=units,
            version=root.get("version", ""),
        ),
        units=units,
        diagnostics=diagnostics,
    )

    base = Affine2D.scale(scale)
    _walk(root, Affine2D.identity(), base, scene, diagnostics, default_layer="0")
    from antcam_rc2.core.io.sanitize import sanitize_scene

    return sanitize_scene(scene)


def _resolve_units(root, diagnostics: ImportDiagnostics) -> tuple[UnitSystem, float]:
    """Resolve the unit system and a scale factor to apply to viewBox units.

    When the ``width``/``height`` attributes carry a physical unit (mm, cm, in,
    pt, px) AND a ``viewBox`` is present, the viewBox user units map to the
    declared physical width: ``scale = physical_width_mm / viewBox_width``.
    Without a viewBox, pixels (96 dpi) are assumed with a warning.
    """
    mm_per_px = 25.4 / 96.0
    width_attr = root.get("width") or ""
    height_attr = root.get("height") or ""
    unit_attr = width_attr + height_attr

    if "in" in unit_attr:
        unit_system = UnitSystem.IMPERIAL
    else:
        unit_system = UnitSystem.METRIC
    # A bare number means pixels (implicit); only a physical unit counts as declared.
    declared = bool(re.search(r"(?:mm|cm|in|pt)", unit_attr))

    width_mm = _physical_width_mm(width_attr, mm_per_px)
    viewbox = root.get("viewBox") or ""
    if declared and width_mm is not None and viewbox:
        numbers = re.findall(r"[-+]?\d*(?:\.\d+)?", viewbox)
        numbers = [float(value) for value in numbers if value not in ("", "+", "-")]
        if len(numbers) == 4 and numbers[2] > 0:
            return unit_system, width_mm / numbers[2]

    if not declared:
        diagnostics.add_warning("SVG units not declared; assuming px (96 dpi)")
    return unit_system, mm_per_px


def _physical_width_mm(width_attr: str, mm_per_px: float) -> float | None:
    """Parse a width attribute into millimetres, or ``None`` when unparseable."""
    match = re.fullmatch(r"\s*([-+]?\d+(?:\.\d+)?)\s*(mm|cm|in|pt|px)?\s*", width_attr)
    if match is None:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    if unit == "mm":
        return value
    if unit == "cm":
        return value * 10.0
    if unit == "in":
        return value * 25.4
    if unit == "pt":
        return value * 25.4 / 72.0
    return value * mm_per_px  # px or bare number


def _walk(
    element,
    parent: Affine2D,
    base: Affine2D,
    scene: GeometryScene,
    diagnostics: ImportDiagnostics,
    default_layer: str,
) -> None:
    transform = _parse_transform(element.get("transform"))
    current = Affine2D.chain(parent, transform)
    layer = element.get("id") or element.get(INKSCAPE_LABEL) or default_layer

    tag = element.tag
    if tag in ("{http://www.w3.org/2000/svg}path", "path"):
        d = element.get("d")
        if d:
            _handle_path(d, current, base, scene, diagnostics, layer)
    elif tag in ("{http://www.w3.org/2000/svg}rect", "rect"):
        _handle_rect(element, current, base, scene, diagnostics, layer)
    elif tag in ("{http://www.w3.org/2000/svg}circle", "circle"):
        _handle_circle(element, current, base, scene, diagnostics, layer)
    elif tag in ("{http://www.w3.org/2000/svg}ellipse", "ellipse"):
        _handle_ellipse(element, current, base, scene, diagnostics, layer)
    elif tag in ("{http://www.w3.org/2000/svg}line", "line"):
        _handle_line(element, current, base, scene, diagnostics, layer)
    elif tag in ("{http://www.w3.org/2000/svg}polyline", "polyline"):
        _handle_polyline(element, current, base, scene, diagnostics, layer)
    elif tag in ("{http://www.w3.org/2000/svg}polygon", "polygon"):
        _handle_polygon(element, current, base, scene, diagnostics, layer)

    for child in element:
        if isinstance(child.tag, str) and child.tag not in ("{http://www.w3.org/2000/svg}defs",):
            _walk(child, current, base, scene, diagnostics, layer)


def _handle_path(
    d: str,
    transform: Affine2D,
    base: Affine2D,
    scene: GeometryScene,
    diagnostics: ImportDiagnostics,
    layer: str,
) -> None:
    try:
        svg_path = parse_path(d)
    except Exception as exc:  # noqa: BLE001 - svgpathtools raises various parse errors
        diagnostics.add_error(f"Failed to parse SVG path: {exc}", entity=layer)
        return

    segments = []
    approximated = False
    for seg in svg_path:
        if isinstance(seg, (SvgLine, Line)):
            segments.append(
                LineSegment(
                    Point2(float(seg.start.real), float(seg.start.imag)),
                    Point2(float(seg.end.real), float(seg.end.imag)),
                )
            )
        elif isinstance(seg, SvgArc):
            # Convert SVG arc to a native Arc via centre construction.
            arc = _svg_arc_to_native(seg)
            if arc is not None:
                segments.append(arc)
            else:
                approximated = True
                segments.extend(_bezier_approx(seg, diagnostics))
        elif isinstance(seg, (CubicBezier, QuadraticBezier)):
            approximated = True
            segments.extend(_bezier_approx(seg, diagnostics))

    if not segments:
        return
    if approximated:
        diagnostics.add_warning("SVG path approximated to polyline", entity=layer)

    # Element transforms are composed AFTER the viewBox->mm base scale, so
    # translate/scale/rotate arguments are expressed in millimetres (a design
    # decision: CAM users think in mm; see mini_groups_transform fixture).
    total = Affine2D.chain(transform, base)
    contour = _closed_contour(segments)
    if contour is not None:
        scene.add_entity(apply(total, contour), layer)


def _svg_arc_to_native(seg) -> Arc | None:
    """Convert an svgpathtools Arc into a native Arc."""
    # svgpathtools Arc already stores the ellipse/arc in its own parameterisation.
    # For a circular arc (rx == ry) we can recover centre/angles directly.
    rx = float(seg.radius.real)
    ry = float(seg.radius.imag)
    if abs(rx - ry) > 1e-9:
        return None  # elliptical arc -> sampled
    if rx <= 0.0:
        return None
    start = Point2(float(seg.start.real), float(seg.start.imag))
    end = Point2(float(seg.end.real), float(seg.end.imag))
    # Use the midpoint + perpendicular construction like the DXF bulge path.
    chord = start.distance_to(end)
    if chord <= 1e-12:
        return None
    # svgpathtools exposes center and angles via its geometric helpers.
    center = Point2(float(seg.center.real), float(seg.center.imag))
    start_angle = math.atan2(start.y - center.y, start.x - center.x)
    end_angle = math.atan2(end.y - center.y, end.x - center.x)
    # svgpathtools sweep flag: 1 = positive angle direction (CCW in SVG y-down).
    ccw = bool(seg.sweep)
    return Arc(center, rx, start_angle, end_angle, ccw)


def _bezier_approx(seg, diagnostics: ImportDiagnostics) -> list[LineSegment]:
    def point_at(t: float) -> Point2:
        pt = seg.point(t)
        return Point2(float(pt.real), float(pt.imag))

    points = approximate_to_polyline(point_at, tolerance=1e-3)
    if len(points) < 2:
        return []
    return [LineSegment(a, b) for a, b in zip(points, points[1:], strict=False)]


def _closed_contour(segments: list) -> Contour | None:
    if len(segments) < 3:
        return None
    return Contour(segments)


def _handle_rect(element, transform, base, scene, diagnostics, layer) -> None:
    try:
        x = float(element.get("x", "0"))
        y = float(element.get("y", "0"))
        w = float(element.get("width", "0"))
        h = float(element.get("height", "0"))
    except ValueError:
        diagnostics.add_error("Invalid <rect> dimensions", entity=layer)
        return
    contour = Contour(
        [
            LineSegment(Point2(x, y), Point2(x + w, y)),
            LineSegment(Point2(x + w, y), Point2(x + w, y + h)),
            LineSegment(Point2(x + w, y + h), Point2(x, y + h)),
            LineSegment(Point2(x, y + h), Point2(x, y)),
        ]
    )
    scene.add_entity(apply(Affine2D.chain(transform, base), contour), layer)


def _handle_circle(element, transform, base, scene, diagnostics, layer) -> None:
    try:
        cx = float(element.get("cx", "0"))
        cy = float(element.get("cy", "0"))
        r = float(element.get("r", "0"))
    except ValueError:
        diagnostics.add_error("Invalid <circle>", entity=layer)
        return
    circle = Circle(Point2(cx, cy), r)
    scene.add_entity(apply(Affine2D.chain(transform, base), circle), layer)


def _handle_ellipse(element, transform, base, scene, diagnostics, layer) -> None:
    try:
        cx = float(element.get("cx", "0"))
        cy = float(element.get("cy", "0"))
        rx = float(element.get("rx", "0"))
        ry = float(element.get("ry", "0"))
    except ValueError:
        diagnostics.add_error("Invalid <ellipse>", entity=layer)
        return
    diagnostics.add_warning("Ellipse approximated to polyline", entity=layer)

    def point_at(t: float) -> Point2:
        angle = 2.0 * math.pi * t
        return Point2(cx + rx * math.cos(angle), cy + ry * math.sin(angle))

    points = approximate_to_polyline(point_at, tolerance=1e-3)
    if len(points) < 3:
        return
    segments: list[Curve2] = [LineSegment(a, b) for a, b in zip(points, points[1:], strict=False)]
    scene.add_entity(apply(Affine2D.chain(transform, base), Contour(segments)), layer)


def _handle_line(element, transform, base, scene, diagnostics, layer) -> None:
    try:
        x1 = float(element.get("x1", "0"))
        y1 = float(element.get("y1", "0"))
        x2 = float(element.get("x2", "0"))
        y2 = float(element.get("y2", "0"))
    except ValueError:
        diagnostics.add_error("Invalid <line>", entity=layer)
        return
    line = LineSegment(Point2(x1, y1), Point2(x2, y2))
    scene.add_entity(apply(Affine2D.chain(transform, base), line), layer)


def _handle_polyline(element, transform, base, scene, diagnostics, layer) -> None:
    points = _parse_points(element.get("points", ""))
    if len(points) < 2:
        diagnostics.add_warning("Empty <polyline> skipped", entity=layer)
        return
    segments = [LineSegment(a, b) for a, b in zip(points, points[1:], strict=False)]
    contour = _closed_contour(segments)
    if contour is None:
        return
    scene.add_entity(apply(Affine2D.chain(transform, base), contour), layer)


def _handle_polygon(element, transform, base, scene, diagnostics, layer) -> None:
    points = _parse_points(element.get("points", ""))
    if len(points) < 3:
        diagnostics.add_warning("Degenerate <polygon> skipped", entity=layer)
        return
    segments: list[Curve2] = [LineSegment(a, b) for a, b in zip(points, points[1:] + [points[0]], strict=False)]
    scene.add_entity(apply(Affine2D.chain(transform, base), Contour(segments)), layer)


def _parse_points(value: str) -> list[Point2]:
    parts = value.replace(",", " ").split()
    points = []
    for i in range(0, len(parts) - 1, 2):
        try:
            points.append(Point2(float(parts[i]), float(parts[i + 1])))
        except ValueError:
            break
    return points


def _parse_transform(value: str | None) -> Affine2D:
    if not value:
        return Affine2D.identity()
    result = Affine2D.identity()
    # Parse simple transform="translate(a b) scale(s) rotate(d)" forms.
    for kind, args in re.findall(r"(\w+)\(([^)]*)\)", value):
        try:
            nums = [float(x) for x in args.replace(",", " ").split() if x.strip()]
            if kind == "translate" and nums:
                tx = nums[0]
                ty = nums[1] if len(nums) > 1 else 0.0
                result = Affine2D.chain(result, Affine2D.translate(tx, ty))
            elif kind == "scale" and nums:
                sx = nums[0]
                sy = nums[1] if len(nums) > 1 else sx
                result = Affine2D.chain(result, Affine2D.scale(sx, sy))
            elif kind == "rotate" and nums:
                result = Affine2D.chain(result, Affine2D.rotate(math.radians(nums[0])))
        except ValueError:
            continue  # malformed numeric arguments: skip that function, keep the rest
    return result
