"""Operations on 2D geometry: bounding boxes, welding, offsets, booleans.

External geometry engines (shapely for booleans, pyclipper for offsets) are
confined to this module so the public geometry API stays native.
"""

from __future__ import annotations

from collections.abc import Iterable

import pyclipper

from antcam_rc2.core.errors import GeometryError
from antcam_rc2.core.geometry.curves import Curve2, LineSegment
from antcam_rc2.core.geometry.paths import Contour, Orientation, Path
from antcam_rc2.core.geometry.primitives import Box2, Point2
from antcam_rc2.core.units import TOLERANCE_MM

# pyclipper ships without type stubs; expose the symbols we need.
_PyclipperOffset = pyclipper.PyclipperOffset  # ty: ignore[unresolved-attribute]
_ET_CLOSEDPOLYGON = pyclipper.ET_CLOSEDPOLYGON  # ty: ignore[unresolved-attribute]
_JOIN_TYPES = {name: getattr(pyclipper, f"JT_{name.upper()}") for name in ("round", "square", "miter")}

# pyclipper operates on integer coordinates; a 1 mm-scale drawing loses any
# sub-millimetre offset.  Scaling coordinates and deltas by this factor keeps
# 1 µm precision while staying well inside the int32 range for CAM-scale input.
_OFFSET_SCALE = 1000.0

__all__ = [
    "bounding_box",
    "weld",
    "orient",
    "offset",
    "union",
    "difference",
    "intersection",
    "boolean",
    "raster_lines",
    "contains_point",
]


def bounding_box(entities: Iterable[Curve2 | Contour | Path]) -> Box2:
    """Compute the axis-aligned bounding box of a collection of entities."""
    boxes: list[Box2] = []
    for entity in entities:
        if isinstance(entity, Path):
            boxes.append(entity.bounding_box())
        elif isinstance(entity, Contour):
            boxes.append(entity.bounding_box())
        elif isinstance(entity, Curve2):
            boxes.append(
                Box2(
                    min(entity.start.x, entity.end.x),
                    min(entity.start.y, entity.end.y),
                    max(entity.start.x, entity.end.x),
                    max(entity.start.y, entity.end.y),
                )
            )
        else:
            raise TypeError(f"Cannot compute bounding box of {type(entity).__name__}")
    if not boxes:
        return Box2()
    result = boxes[0]
    for box in boxes[1:]:
        result = result.union(box)
    return result


def weld(points: Iterable[Point2], tolerance: float = TOLERANCE_MM) -> list[Point2]:
    """Merge points that fall within ``tolerance`` of each other.

    Points are processed in order; the first point of a cluster is kept.
    Returns a new list of points.
    """
    merged: list[Point2] = []
    for point in points:
        if all(point.distance_to(existing) > tolerance for existing in merged):
            merged.append(point)
    return merged


def _align_junctions(contour: Contour, tolerance: float) -> Contour:
    """Snap segment junctions so consecutive segments share an endpoint.

    Curves are re-created with the previous segment's end point when they start
    within ``tolerance`` of it.  This is a localised weld that keeps arcs and
    lines intact.
    """
    if not contour.segments:
        return contour
    new_segments: list[Curve2] = []
    for seg in contour.segments:
        prev_end = new_segments[-1].end if new_segments else None
        if prev_end is not None and seg.start.distance_to(prev_end) <= tolerance:
            seg = _resnap(seg, prev_end)
        new_segments.append(seg)
    return Contour(new_segments)


def _resnap(seg: Curve2, start: Point2) -> Curve2:
    """Re-create ``seg`` starting at ``start`` while keeping its shape."""
    from antcam_rc2.core.geometry.curves import Arc, Circle

    if isinstance(seg, LineSegment):
        return LineSegment(start, seg.end)
    if isinstance(seg, Arc):
        # Rebuild the arc using the new start; end angle and radius unchanged.
        offset = start - seg.start
        return Arc(
            Point2(seg.center.x + offset.x, seg.center.y + offset.y),
            seg.radius,
            seg.start_angle,
            seg.end_angle,
            seg.ccw,
        )
    if isinstance(seg, Circle):
        return Circle(start, seg.radius)
    raise TypeError(f"Cannot re-snap segment of type {type(seg).__name__}")


def orient(contour: Contour, convention: Orientation = Orientation.CCW) -> Contour:
    """Return the contour normalised to the requested loop orientation.

    External boundaries are conventionally ``CCW``; holes ``CW``.
    """
    if contour.orientation() is not convention:
        return contour.reverse()
    return contour


def offset(
    contour: Contour | Path,
    distance: float,
    *,
    join_type: str = "round",
    miter_limit: float = 2.0,
    arc_tolerance_mm: float = 0.01,
) -> list[Contour]:
    """Offset a contour or path by ``distance`` (mm).

    Uses pyclipper's exact ClipperOffset with parallel-curve semantics suited
    for CAM (profiling / pocketing).  A positive ``distance`` expands outward
    for CCW loops; negative shrinks.

    Args:
        contour: the source loop(s).
        distance: offset distance in mm (can be negative).
        join_type: one of ``"round"``, ``"square"``, ``"miter"``.
        miter_limit: miter limit (only used with ``"miter"``).
        arc_tolerance_mm: maximum chord error of rounded join arc segments;
            bounds the polyline resolution so sub-mm precision does not explode
            the vertex count.

    Returns:
        The resulting loops as contours.
    """
    from antcam_rc2.core.geometry.paths import _points_to_contour

    if distance == 0.0:
        raise GeometryError("Offset distance must be non-zero")

    subject: list[list[tuple[float, float]]] = []
    sources = contour.contours if isinstance(contour, Path) else [contour]
    for c in sources:
        pts = [(p.x * _OFFSET_SCALE, p.y * _OFFSET_SCALE) for p in c.to_polyline()]
        if pts and pts[0] != pts[-1]:
            pts.append(pts[0])
        subject.append(pts)

    pc = _PyclipperOffset(miter_limit, max(arc_tolerance_mm, 1e-6) * _OFFSET_SCALE)
    join = _join_type(join_type)
    for loop in subject:
        pc.AddPath(loop, join, _ET_CLOSEDPOLYGON)
    solved = pc.Execute(distance * _OFFSET_SCALE)

    return [_points_to_contour([Point2(x / _OFFSET_SCALE, y / _OFFSET_SCALE) for x, y in loop]) for loop in solved]


def _join_type(join_type: str) -> int:
    if join_type not in _JOIN_TYPES:
        raise ValueError(f"Unknown join type: {join_type!r}")
    return _JOIN_TYPES[join_type]


def union(contours: Iterable[Contour]) -> list[Contour]:
    """Boolean union of a set of closed contours (shapely-based)."""
    return boolean(list(contours), [], "union")


def difference(subject: Iterable[Contour], clips: Iterable[Contour]) -> list[Contour]:
    """Subtract ``clips`` from ``subject`` (shapely-based)."""
    return boolean(list(subject), list(clips), "difference")


def intersection(subject: Iterable[Contour], clips: Iterable[Contour]) -> list[Contour]:
    """Intersection of two sets of closed contours (shapely-based)."""
    return boolean(list(subject), list(clips), "intersection")


def contains_point(region: Contour, point: Point2) -> bool:
    """True when ``point`` lies inside the closed region (boundary included).

    Shapely is confined to this module; the public API stays native.
    """
    import shapely.geometry as shgeo

    polygon = _contour_to_polygon(region)
    if polygon.is_empty:
        return False
    return polygon.covers(shgeo.Point(point.x, point.y))


def raster_lines(region: Contour, stepover_mm: float, *, margin_mm: float = 0.0) -> list[list[Point2]]:
    """Rasterize a closed region into parallel horizontal lines.

    Lines are spaced ``stepover_mm`` apart inside the region's bounding box
    and clipped to the region polygon (shapely, confined to this module).
    The result is a list of open polylines suitable for facing passes.
    """
    import shapely.geometry as shgeo

    if stepover_mm <= 0:
        raise GeometryError("raster stepover_mm must be positive")
    polygon = _contour_to_polygon(region)
    if polygon.is_empty:
        return []
    bounds = polygon.bounds  # (minx, miny, maxx, maxy)
    min_x, min_y, max_x, max_y = bounds
    if max_x - min_x <= TOLERANCE_MM or max_y - min_y <= TOLERANCE_MM:
        return []

    lines: list[list[Point2]] = []
    y = min_y + stepover_mm / 2.0 + margin_mm
    while y <= max_y - margin_mm:
        segment = shgeo.LineString([(min_x - margin_mm, y), (max_x + margin_mm, y)])
        clipped = polygon.intersection(segment)
        if not clipped.is_empty:
            for geom in clipped.geoms if hasattr(clipped, "geoms") else (clipped,):
                coords = list(geom.coords)
                if len(coords) >= 2:
                    lines.append([Point2(x, y_value) for x, y_value in coords])
        y += stepover_mm
    return lines


def boolean(subject: list[Contour], clips: list[Contour], op: str) -> list[Contour]:
    """Generic boolean operation between closed contour sets.

    Args:
        subject: primary contours.
        clips: secondary contours (unused for ``"union"``).
        op: one of ``"union"``, ``"difference"``, ``"intersection"``.

    Returns:
        Resulting contours (outer and holes resolved).
    """
    from antcam_rc2.core.geometry.paths import _polygon_to_contours

    if op not in {"union", "difference", "intersection"}:
        raise ValueError(f"Unknown boolean op: {op!r}")

    subject_polys = [_contour_to_polygon(c) for c in subject]
    clip_polys = [_contour_to_polygon(c) for c in clips]

    result = _apply_boolean(subject_polys, clip_polys, op)
    geoms = list(result.geoms) if hasattr(result, "geoms") else [result]
    contours: list[Contour] = []
    for poly in geoms:
        if not poly.is_empty:
            contours.extend(_polygon_to_contours(poly))
    return contours


def _contour_to_polygon(contour: Contour):
    import shapely.geometry as shgeo

    ring = [(p.x, p.y) for p in contour.to_polyline()]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    if len(ring) < 4:
        raise GeometryError("Cannot build a polygon from fewer than 3 distinct points")
    return shgeo.Polygon(ring)


def _apply_boolean(subject_polys: list, clip_polys: list, op: str):
    import shapely.ops as shops

    if op == "union":
        return shops.unary_union(subject_polys + clip_polys)
    if op == "intersection":
        merged = shops.unary_union(subject_polys)
        for poly in clip_polys:
            merged = merged.intersection(poly)
        return merged
    # difference
    merged = shops.unary_union(subject_polys)
    if clip_polys:
        merged = merged.difference(shops.unary_union(clip_polys))
    return merged
