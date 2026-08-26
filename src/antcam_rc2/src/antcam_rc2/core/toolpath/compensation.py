"""Tool center compensation: converts geometry to tool-center paths for 2.5D operations."""

from __future__ import annotations

from antcam_rc2.core.databases.models import Tool
from antcam_rc2.core.errors import ToolpathError
from antcam_rc2.core.geometry.curves import Curve2, LineSegment
from antcam_rc2.core.geometry.ops import offset, raster_lines
from antcam_rc2.core.geometry.paths import Contour, Orientation
from antcam_rc2.core.geometry.primitives import Point2


def tool_radius_mm(tool: Tool) -> float:
    """Return the effective cutting radius of the tool."""
    return tool.cutting_diameter_mm / 2.0


def profile_offset(
    contour: Contour,
    tool: Tool,
    side: str = "outside",
) -> Contour | None:
    """Offset a single closed contour for profiling.

    Args:
        contour: the geometry contour to offset.
        tool: the tool providing the radius.
        side: ``"outside"`` for external profiles, ``"inside"`` for internal loops.

    Returns:
        The tool-centre contour, or ``None`` when the offset degenerates.

    Raises:
        ToolpathError: when the offset produces multiple loops or fails.
    """
    radius = tool_radius_mm(tool)
    distance = radius if side == "outside" else -radius
    try:
        results = offset(contour, distance)
    except Exception as exc:
        raise ToolpathError(f"profile offset failed: {exc}") from exc
    if not results:
        return None
    if len(results) > 1:
        raise ToolpathError(f"profile offset produced {len(results)} loops, expected 1")
    return results[0]


def pocket_offsets(
    region: Contour,
    tool: Tool,
    stepover_mm: float,
    max_iterations: int = 1000,
) -> list[Contour]:
    """Generate inward offset loops for pocket clearing.

    The first offset removes the tool radius; subsequent offsets advance by
    ``stepover_mm``.  A region exhausted by the offset (loop shrinks below the
    tool) terminates the iteration normally.

    Raises:
        ToolpathError: when the initial radius offset fails (region too small)
            or the iteration limit is exceeded.
    """
    if not region.closed:
        raise ToolpathError("pocket region must be closed")
    if stepover_mm <= 0:
        raise ToolpathError("stepover_mm must be positive")

    radius = tool_radius_mm(tool)
    first_distance = -radius if region.orientation() is Orientation.CCW else radius
    try:
        current = offset(region, first_distance)
    except Exception as exc:
        raise ToolpathError(f"initial pocket offset failed: {exc}") from exc
    if not current:
        raise ToolpathError("pocket geometry is too small for the selected tool")

    loops: list[Contour] = list(current)
    iteration = 0
    while iteration < max_iterations:
        iteration += 1
        next_loops: list[Contour] = []
        for loop in current:
            distance = -stepover_mm if loop.orientation() is Orientation.CCW else stepover_mm
            try:
                next_loops.extend(offset(loop, distance))
            except Exception:
                continue  # this loop is exhausted; the region terminated here
        if not next_loops:
            break
        # Near the vanishing point the offset can flip loop orientation and
        # start expanding; stop before emitting any growing loop.
        previous_area = max(_loop_area(loop) for loop in current)
        next_area = max(_loop_area(loop) for loop in next_loops)
        if next_area >= previous_area:
            break
        current = next_loops
        loops.extend(current)
    if iteration >= max_iterations:
        raise ToolpathError("pocket offset iteration limit exceeded")
    return loops


def _loop_area(contour: Contour) -> float:
    """Absolute signed area used for monotonic-shrink termination checks."""
    box = contour.bounding_box()
    return abs(box.width * box.height)


def facing_region_offset(
    region: Contour,
    tool: Tool,
    stepover_mm: float,
) -> list[Contour]:
    """Rasterize a region into parallel facing passes.

    Each raster line becomes an open contour cut at depth; the tool radius is
    respected by insetting the region before rasterization.
    """
    if stepover_mm <= 0:
        raise ToolpathError("stepover_mm must be positive")
    radius = tool_radius_mm(tool)
    try:
        inset = offset(region, -radius if region.orientation() is Orientation.CCW else radius)
    except Exception as exc:
        raise ToolpathError(f"facing inset failed: {exc}") from exc
    if not inset:
        raise ToolpathError("facing region is too small for the selected tool")
    lines: list[Contour] = []
    for loop in inset:
        for polyline in raster_lines(loop, stepover_mm, margin_mm=radius * 0.1):
            segments: list[Curve2] = [
                LineSegment(a, b) for a, b in zip(polyline, polyline[1:], strict=False) if a.distance_to(b) > 1e-9
            ]
            if segments:
                lines.append(Contour(segments))
    return lines


def slot_geometry(
    contour: Contour,
    tool: Tool,
    stepover_mm: float,
) -> list[list[Point2]]:
    """Decompose a slot contour into centerline or multi-lane passes.

    The slot axis is the long axis of the bounding box.  A slot narrower than
    the tool diameter cannot be machined (raises); a slot up to one tool
    diameter wide is a single centerline pass; wider slots are rasterized with
    ``stepover_mm`` lanes along the long axis.

    Returns a list of open polylines (tool-centre paths).
    """
    if not contour.closed:
        raise ToolpathError("slot region must be closed")
    if stepover_mm <= 0:
        raise ToolpathError("stepover_mm must be positive")
    radius = tool_radius_mm(tool)
    bbox = contour.bounding_box()
    slot_width = min(bbox.width, bbox.height)
    if slot_width < radius * 2.0:
        raise ToolpathError("slot is narrower than the tool diameter")
    horizontal = bbox.width >= bbox.height
    if slot_width <= radius * 2.0 * 1.05:
        # Single centerline along the long axis.
        if horizontal:
            y_center = (bbox.min_y + bbox.max_y) / 2.0
            centerline = [Point2(bbox.min_x, y_center), Point2(bbox.max_x, y_center)]
        else:
            x_center = (bbox.min_x + bbox.max_x) / 2.0
            centerline = [Point2(x_center, bbox.min_y), Point2(x_center, bbox.max_y)]
        return [centerline]

    # Multi-lane raster along the long axis, offset from the center by lanes.
    lanes: list[list[Point2]] = []
    if horizontal:
        y = bbox.min_y + radius
        while y <= bbox.max_y - radius + 1e-9:
            lanes.append([Point2(bbox.min_x, y), Point2(bbox.max_x, y)])
            y += stepover_mm
    else:
        x = bbox.min_x + radius
        while x <= bbox.max_x - radius + 1e-9:
            lanes.append([Point2(x, bbox.min_y), Point2(x, bbox.max_y)])
            x += stepover_mm
    if not lanes:
        raise ToolpathError("slot has no room for any tool lane")
    return lanes


def slot_centerline(
    contour: Contour,
    tool: Tool,
) -> Contour | None:
    """Backwards-compatible helper: return the single centerline, if any.

    Returns ``None`` when the slot is too narrow for the tool or requires
    multiple lanes; use :func:`slot_geometry` for the full decomposition.
    """
    try:
        polylines = slot_geometry(contour, tool, tool.cutting_diameter_mm)
    except ToolpathError:
        return None
    if len(polylines) != 1:
        return None
    polyline = polylines[0]
    segments: list[Curve2] = [
        LineSegment(a, b) for a, b in zip(polyline, polyline[1:], strict=False) if a.distance_to(b) > 1e-9
    ]
    if not segments:
        return None
    return Contour(segments)


def validate_tool_fits(
    contour: Contour,
    tool: Tool,
    min_feature_size_mm: float = 0.1,
) -> tuple[bool, str | None]:
    """Check whether the tool can cut the geometry without gouging.

    Returns ``(fits, error_message_if_not)``.
    """
    radius = tool_radius_mm(tool)
    try:
        inward = offset(contour, -radius)
        outward = offset(contour, radius)
        if not inward and not outward:
            return False, f"tool diameter {tool.cutting_diameter_mm:.3f}mm too large for geometry"
        if not inward:
            return False, f"tool too large for internal features (diameter {tool.cutting_diameter_mm:.3f}mm)"
    except Exception as exc:
        return False, f"offset validation failed: {exc}"
    return True, None
