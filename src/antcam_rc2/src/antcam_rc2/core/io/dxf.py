"""DXF importer using ezdxf.

Maps DXF entities to native geometry primitives:

- ``LINE`` -> :class:`LineSegment`
- ``ARC`` -> :class:`Arc`
- ``CIRCLE`` -> :class:`Circle`
- ``LWPOLYLINE`` / ``POLYLINE`` -> :class:`Contour` (bulge converted to arcs)
- ``ELLIPSE`` / ``SPLINE`` -> polyline sampled adaptively
- ``INSERT`` -> block exploded into its defining entities
- ``HATCH`` / ``TEXT`` / ``MTEXT`` / ``DIMENSION`` / ``POINT`` -> skipped with a warning

Units come from the ``$INSUNITS`` header; unknown or unitless files default to
metric with a warning.  All entities are collected into a
:class:`GeometryScene` with layer information preserved.
"""

from __future__ import annotations

import math
from pathlib import Path

import ezdxf
import ezdxf.recover

from antcam_rc2.core.geometry.curves import Arc, Circle, LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.geometry.sampling import approximate_to_polyline
from antcam_rc2.core.io.diagnostics import ImportDiagnostics
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.units import UnitSystem

__all__ = ["import_dxf"]

_UNIT_CODES: dict[int, UnitSystem] = {
    1: UnitSystem.IMPERIAL,  # inches
    2: UnitSystem.IMPERIAL,  # feet
    4: UnitSystem.METRIC,  # millimetres
    5: UnitSystem.METRIC,  # centimetres
    6: UnitSystem.METRIC,  # metres
    7: UnitSystem.METRIC,  # kilometres
}

_SKIPPED_TYPES = {"HATCH", "TEXT", "MTEXT", "DIMENSION", "POINT", "ATTRIB", "ATTDEF"}


def import_dxf(path: str | Path) -> GeometryScene:
    """Import a DXF file into a :class:`GeometryScene`."""
    file_path = Path(path)
    diagnostics = ImportDiagnostics()
    try:
        doc, _auditor = ezdxf.recover.readfile(file_path)
    except AttributeError:
        doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()

    units = _resolve_units(doc, diagnostics)

    scene = GeometryScene(
        source=SourceInfo(
            format="dxf",
            path=str(file_path),
            original_units=units,
            version=f"ezdxf {ezdxf.__version__}",
        ),
        units=units,
        diagnostics=diagnostics,
    )

    _collect(msp, scene, diagnostics, block_stack=())
    from antcam_rc2.core.io.sanitize import sanitize_scene

    return sanitize_scene(scene)


def _resolve_units(doc, diagnostics: ImportDiagnostics) -> UnitSystem:
    code = doc.header.get("$INSUNITS", 0)
    if code in _UNIT_CODES:
        return _UNIT_CODES[code]
    diagnostics.add_warning(f"Unknown $INSUNITS={code}; defaulting to metric")
    return UnitSystem.METRIC


def _collect(entities, scene: GeometryScene, diagnostics: ImportDiagnostics, block_stack: tuple) -> None:
    for entity in entities:
        layer_name = getattr(entity.dxf, "layer", "0")
        try:
            _handle_entity(entity, scene, diagnostics, layer_name, block_stack)
        except Exception as exc:  # noqa: BLE001 - keep one bad entity from killing the import
            diagnostics.add_error(f"Failed to import {entity.dxftype()} #{entity.dxf.handle}: {exc}", entity=layer_name)


def _handle_entity(
    entity,
    scene: GeometryScene,
    diagnostics: ImportDiagnostics,
    layer: str,
    block_stack: tuple,
) -> None:
    dtype = entity.dxftype()
    if dtype in _SKIPPED_TYPES:
        diagnostics.add_warning(f"{dtype} skipped (out of 2D CAM scope)", entity=layer)
        return
    if dtype == "LINE":
        scene.add_entity(
            LineSegment(
                Point2(entity.dxf.start.x, entity.dxf.start.y),
                Point2(entity.dxf.end.x, entity.dxf.end.y),
            ),
            layer,
        )
    elif dtype == "CIRCLE":
        scene.add_entity(
            Circle(Point2(entity.dxf.center.x, entity.dxf.center.y), entity.dxf.radius),
            layer,
        )
    elif dtype == "ARC":
        scene.add_entity(
            Arc(
                Point2(entity.dxf.center.x, entity.dxf.center.y),
                entity.dxf.radius,
                math.radians(entity.dxf.start_angle),
                math.radians(entity.dxf.end_angle),
                ccw=True,
            ),
            layer,
        )
    elif dtype == "LWPOLYLINE":
        scene.add_entity(_lwpolyline_to_contour(entity), layer)
    elif dtype == "POLYLINE":
        scene.add_entity(_polyline_to_contour(entity), layer)
    elif dtype == "ELLIPSE":
        pts = _sample_ellipse(entity)
        if len(pts) >= 3:
            scene.add_entity(Contour([LineSegment(a, b) for a, b in zip(pts, pts[1:], strict=False)]), layer)
        else:
            diagnostics.add_warning("ELLIPSE too degenerate to sample", entity=layer)
    elif dtype == "SPLINE":
        pts = _sample_spline(entity)
        if len(pts) >= 3:
            scene.add_entity(Contour([LineSegment(a, b) for a, b in zip(pts, pts[1:], strict=False)]), layer)
        else:
            diagnostics.add_warning("SPLINE too degenerate to sample", entity=layer)
    elif dtype == "INSERT":
        _handle_insert(entity, scene, diagnostics, layer, block_stack)
    else:
        diagnostics.add_warning(f"{dtype} not supported yet, skipped", entity=layer)


def _lwpolyline_to_contour(entity) -> Contour:
    segments = []
    points = list(entity.get_points())
    closed = bool(entity.closed)
    for i, point in enumerate(points):
        start = Point2(point[0], point[1])
        if i + 1 < len(points):
            end_point = points[i + 1]
            end = Point2(end_point[0], end_point[1])
        elif closed:
            end = Point2(points[0][0], points[0][1])
        else:
            break
        bulge = point[4] if len(point) > 4 else 0.0
        segments.append(_bulge_segment(start, end, bulge))
    return Contour(segments)


def _polyline_to_contour(entity) -> Contour:
    vertices = list(entity.vertices)
    segments = []
    closed = entity.is_closed if hasattr(entity, "is_closed") else bool(getattr(entity, "closed", False))
    for i, vertex in enumerate(vertices):
        start = Point2(vertex.dxf.location.x, vertex.dxf.location.y)
        if i + 1 < len(vertices):
            end = Point2(vertices[i + 1].dxf.location.x, vertices[i + 1].dxf.location.y)
        elif closed:
            end = Point2(vertices[0].dxf.location.x, vertices[0].dxf.location.y)
        else:
            break
        bulge = getattr(vertex.dxf, "bulge", 0.0)
        segments.append(_bulge_segment(start, end, bulge))
    return Contour(segments)


def _bulge_segment(start: Point2, end: Point2, bulge: float) -> LineSegment | Arc:
    """Convert a segment with a DXF bulge into a line or arc.

    DXF bulge is ``tan(sweep/4)``; positive bulge sweeps counter-clockwise.
    """
    if bulge == 0.0:
        return LineSegment(start, end)
    # sweep = 4 * atan(bulge)
    sweep = 4.0 * math.atan(bulge)
    ccw = sweep > 0.0
    abs_sweep = abs(sweep)
    chord = start.distance_to(end)
    radius = chord / (2.0 * math.sin(abs_sweep / 2.0)) if abs_sweep not in (0.0, math.pi) else chord / 2.0

    # Centre: mid-chord + perpendicular offset of distance h = radius*cos(sweep/2)
    mid = Point2((start.x + end.x) / 2.0, (start.y + end.y) / 2.0)
    perp = Point2(-(end.y - start.y), end.x - start.x)
    perp_len = perp.distance_to(Point2(0, 0))
    if perp_len <= 1e-12:
        return LineSegment(start, end)
    h = radius * math.cos(abs_sweep / 2.0)
    direction = 1.0 if ccw else -1.0
    center = Point2(
        mid.x + direction * perp.x / perp_len * h,
        mid.y + direction * perp.y / perp_len * h,
    )
    start_angle = math.atan2(start.y - center.y, start.x - center.x)
    end_angle = math.atan2(end.y - center.y, end.x - center.x)
    return Arc(center, radius, start_angle, end_angle, ccw)


def _sample_ellipse(entity) -> list[Point2]:
    center = entity.dxf.center
    major = entity.dxf.major_axis
    ratio = entity.dxf.ratio
    start_param = entity.dxf.start_param
    end_param = entity.dxf.end_param
    radius_x = math.hypot(major.x, major.y)
    if radius_x <= 1e-12:
        return []
    theta = math.atan2(major.y, major.x)

    def point_at(t: float) -> Point2:
        param = start_param + (end_param - start_param) * t
        ex = radius_x * math.cos(param)
        ey = radius_x * ratio * math.sin(param)
        x = center.x + ex * math.cos(theta) - ey * math.sin(theta)
        y = center.y + ex * math.sin(theta) + ey * math.cos(theta)
        return Point2(x, y)

    return approximate_to_polyline(point_at, tolerance=1e-3)


def _sample_spline(entity) -> list[Point2]:
    """Flatten a spline to a polyline using ezdxf's own flattening (max 0.5 mm)."""
    try:
        points = entity.flattening(0.5)
        return [Point2(p.x, p.y) for p in points]
    except Exception:
        return []


def _handle_insert(
    entity,
    scene: GeometryScene,
    diagnostics: ImportDiagnostics,
    layer: str,
    block_stack: tuple,
) -> None:
    block_name = entity.dxf.name
    try:
        block = entity.doc.blocks[block_name]
    except KeyError:
        diagnostics.add_error(f"Block {block_name!r} not defined", entity=layer)
        return
    if block_name in block_stack:
        diagnostics.add_warning(f"Recursive block insert skipped: {block_name!r}", entity=layer)
        return

    block_entities = list(block)
    if not block_entities:
        diagnostics.add_warning(f"Block {block_name!r} is empty", entity=layer)
        return

    from antcam_rc2.core.geometry.transform import Affine2D, apply

    tx = entity.dxf.insert.x
    ty = entity.dxf.insert.y
    rotation = math.radians(entity.dxf.rotation)
    scale_x = getattr(entity.dxf, "xscale", 1.0)
    scale_y = getattr(entity.dxf, "yscale", 1.0)

    transform = Affine2D.chain(
        Affine2D.translate(tx, ty),
        Affine2D.rotate(rotation),
        Affine2D.scale(scale_x, scale_y),
    )

    for be in block_entities:
        sub_layer = getattr(be.dxf, "layer", "0") or layer
        sub_scene = GeometryScene(source=scene.source)
        try:
            _handle_entity(be, sub_scene, diagnostics, sub_layer, (*block_stack, block_name))
        except Exception as exc:  # noqa: BLE001
            diagnostics.add_error(f"Failed to import block entity {be.dxftype()}: {exc}", entity=sub_layer)
            continue
        for sub_layer_item in sub_scene.layers:
            for geometry_entity in sub_layer_item.entities:
                scene.add_entity(apply(transform, geometry_entity), sub_layer_item.name)
