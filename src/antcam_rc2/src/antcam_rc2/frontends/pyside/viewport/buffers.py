"""Pure GPU buffer construction from a :class:`RenderScene`.

All functions are numpy-only and unit-testable without a GL context: they
expand the neutral graph into the interleaved vertex arrays consumed by the
viewport renderer.  A single array per pipeline is uploaded once and reused
across frames (delta updates rebuild only the affected pipelines).
"""

from __future__ import annotations

import numpy as np

from antcam_rc2.core.rendering.scene_graph import NodeKind, RenderNode, RenderScene

# Vertex strides (floats per vertex) — keep in sync with the GLSL attribute layouts.
_LINE_STRIDE = 8  # x y z  r g b a
_SOLID_STRIDE = 10  # x y z  nx ny nz  r g b a
_PICK_STRIDE = 7  # x y z  id_r id_g id_b id_a

# Digit segments for 0-9 using a simple 5x7 grid (each digit is 5 wide x 7 high)
# Each segment is ((x1, y1), (x2, y2)) in local digit coordinates (0..5, 0..7)
_DIGIT_SEGMENTS = {
    "0": [
        (0, 0, 5, 0),
        (5, 0, 5, 7),
        (5, 7, 0, 7),
        (0, 7, 0, 0),
        (0, 0, 0, 7),
        (5, 0, 5, 7),  # verticals
    ],
    "1": [
        (3, 0, 3, 7),
        (5, 0, 3, 0),  # main vertical + top
    ],
    "2": [
        (0, 0, 5, 0),
        (5, 0, 5, 3.5),
        (5, 3.5, 0, 3.5),
        (0, 3.5, 0, 7),
        (0, 7, 5, 7),
    ],
    "3": [
        (0, 0, 5, 0),
        (5, 0, 5, 3.5),
        (5, 3.5, 0, 3.5),
        (5, 3.5, 5, 7),
        (0, 7, 5, 7),
    ],
    "4": [
        (0, 0, 0, 3.5),
        (0, 3.5, 5, 3.5),
        (5, 0, 5, 7),
    ],
    "5": [
        (5, 0, 0, 0),
        (0, 0, 0, 3.5),
        (0, 3.5, 5, 3.5),
        (5, 3.5, 5, 7),
        (5, 7, 0, 7),
    ],
    "6": [
        (5, 0, 0, 0),
        (0, 0, 0, 7),
        (0, 7, 5, 7),
        (5, 7, 5, 3.5),
        (5, 3.5, 0, 3.5),
    ],
    "7": [
        (0, 0, 5, 0),
        (5, 0, 5, 7),
    ],
    "8": [
        (0, 0, 5, 0),
        (5, 0, 5, 7),
        (5, 7, 0, 7),
        (0, 7, 0, 0),
        (0, 3.5, 5, 3.5),
    ],
    "9": [
        (0, 0, 5, 0),
        (5, 0, 5, 7),
        (5, 7, 0, 7),
        (0, 7, 0, 3.5),
        (0, 3.5, 5, 3.5),
    ],
    "-": [
        (0, 3.5, 5, 3.5),
    ],
}


def _digit_segments_to_vertices(
    digit: str, origin_x: float, origin_y: float, z: float, scale: float, color: tuple
) -> np.ndarray:
    """Convert digit segments to line vertices."""
    if digit not in _DIGIT_SEGMENTS:
        return np.empty((0, _LINE_STRIDE), dtype=np.float32)
    segments = _DIGIT_SEGMENTS[digit]
    vertices = []
    for x1, y1, x2, y2 in segments:
        # Scale and translate
        sx1 = origin_x + x1 * scale
        sy1 = origin_y + y1 * scale
        sx2 = origin_x + x2 * scale
        sy2 = origin_y + y2 * scale
        vertices.append((sx1, sy1, z))
        vertices.append((sx2, sy2, z))
    if not vertices:
        return np.empty((0, _LINE_STRIDE), dtype=np.float32)
    arr = np.array(vertices, dtype=np.float32)
    buffer = np.empty((len(arr), _LINE_STRIDE), dtype=np.float32)
    buffer[:, 0:3] = arr
    buffer[:, 3:7] = np.asarray(color, dtype=np.float32)
    return buffer


def _number_to_digit_vertices(
    number: float, origin_x: float, origin_y: float, z: float, scale: float, color: tuple, decimal_places: int = 0
) -> np.ndarray:
    """Generate vertices for a number as digit line segments."""
    if decimal_places > 0:
        s = f"{number:.{decimal_places}f}"
    else:
        s = f"{int(round(number))}"

    digit_width = 5 * scale
    digit_spacing = 6 * scale
    all_vertices = []

    for i, ch in enumerate(s):
        x_offset = i * digit_spacing
        all_vertices.append(_digit_segments_to_vertices(ch, origin_x + x_offset, origin_y, z, scale, color))

    if not all_vertices:
        return np.empty((0, _LINE_STRIDE), dtype=np.float32)
    return np.concatenate([v for v in all_vertices if v.size > 0], axis=0)


def line_vertices(scene: RenderScene, *, picking: bool = False) -> np.ndarray:
    """Build one interleaved line array for every line-like node.

    Line strips are expanded to ``GL_LINES`` pairs.  In ``picking`` mode the
    color channel carries the picking id encoded as RGBA bytes.
    """
    rows: list[np.ndarray] = []
    stride = _PICK_STRIDE if picking else _LINE_STRIDE
    for node in scene.nodes:
        segments = _line_segments(node)
        if segments.shape[0] == 0:
            continue
        color = _pick_rgba(node.picking_id) if picking else node.color
        vertex_count = len(segments) * 2
        buffer = np.empty((vertex_count, stride), dtype=np.float32)
        buffer[:, 0:3] = segments.reshape(-1, 3)
        buffer[:, 3:7] = np.asarray(color, dtype=np.float32)
        rows.append(buffer)
    if not rows:
        return np.empty((0, stride), dtype=np.float32)
    return np.concatenate(rows, axis=0)


def solid_vertices(scene: RenderScene, *, translucent_only: bool = False) -> np.ndarray:
    """Build one interleaved triangle array for every solid box node.

    Each box becomes 36 vertices (12 triangles) with per-face normals.
    ``translucent_only`` selects nodes whose alpha < 1 (drawn in a separate
    blended pass).
    """
    rows: list[np.ndarray] = []
    for node in scene.nodes:
        if node.kind is not NodeKind.SOLID_BOX or node.box is None:
            continue
        is_translucent = node.color[3] < 0.999
        if is_translucent != translucent_only:
            continue
        rows.append(_box_vertices(node))
    if not rows:
        return np.empty((0, _SOLID_STRIDE), dtype=np.float32)
    return np.concatenate(rows, axis=0)


def grid_vertices(box, spacing_mm: float, *, major_spacing_mm: float | None = None) -> np.ndarray:
    """Build line vertices for a flat grid on the ``z = box.min_z`` plane.

    Major lines (every ``major_spacing_mm``) are brighter than minor lines,
    giving a CAD-style reference grid; the vertex count and layout are
    unchanged (each segment still expands to two ``(x, y, z, r, g, b, a)``
    vertices).
    """
    if box.is_empty or spacing_mm <= 0:
        return np.empty((0, _LINE_STRIDE), dtype=np.float32)
    if major_spacing_mm is None:
        major_spacing_mm = spacing_mm * 5.0
    major_color = (0.42, 0.44, 0.50, 0.9)
    minor_color = (0.20, 0.21, 0.24, 0.65)
    center_x = (box.min_x + box.max_x) / 2.0
    center_y = (box.min_y + box.max_y) / 2.0
    z = box.min_z
    rows: list[list[tuple[float, float, float]]] = []
    colors: list[tuple[float, float, float, float]] = []
    for y in _centered_steps(center_y, box.min_y, box.max_y, spacing_mm):
        rows.append([(box.min_x, y, z), (box.max_x, y, z)])
        colors.append(major_color if _is_major(center_y, y, major_spacing_mm) else minor_color)
    for x in _centered_steps(center_x, box.min_x, box.max_x, spacing_mm):
        rows.append([(x, box.min_y, z), (x, box.max_y, z)])
        colors.append(major_color if _is_major(center_x, x, major_spacing_mm) else minor_color)
    segments = np.asarray(rows, dtype=np.float32).reshape(-1, 3)
    buffer = np.empty((len(segments), _LINE_STRIDE), dtype=np.float32)
    buffer[:, 0:3] = segments
    buffer[:, 3:7] = np.repeat(np.asarray(colors, dtype=np.float32), 2, axis=0)
    return buffer


def grid_label_vertices(
    box,
    spacing_mm: float,
    *,
    major_spacing_mm: float | None = None,
    label_scale_mm: float = 2.0,
    label_color: tuple = (0.6, 0.65, 0.7, 0.8),
) -> np.ndarray:
    """Build line vertices for coordinate labels on major grid lines.

    Labels are placed at the intersection of major grid lines with the grid boundary.
    Each digit is rendered as line segments.
    """
    if box.is_empty or spacing_mm <= 0:
        return np.empty((0, _LINE_STRIDE), dtype=np.float32)
    if major_spacing_mm is None:
        major_spacing_mm = spacing_mm * 5.0

    center_x = (box.min_x + box.max_x) / 2.0
    center_y = (box.min_y + box.max_y) / 2.0
    z = box.min_z + 0.1  # Slightly above grid to avoid z-fighting

    all_vertices = []

    # Y-axis labels (along left edge, showing Y coordinate)
    for y in _centered_steps(center_y, box.min_y, box.max_y, major_spacing_mm):
        if not _is_major(center_y, y, major_spacing_mm):
            continue
        # Place label at left edge, slightly offset
        label_x = box.min_x - 8 * label_scale_mm  # 5 width + 1 spacing per digit, approx
        label_y = y - 3.5 * label_scale_mm  # Center vertically on digit height (7 * scale)
        verts = _number_to_digit_vertices(y, label_x, label_y, z, label_scale_mm, label_color)
        if verts.size > 0:
            all_vertices.append(verts)

    # X-axis labels (along bottom edge, showing X coordinate)
    for x in _centered_steps(center_x, box.min_x, box.max_x, major_spacing_mm):
        if not _is_major(center_x, x, major_spacing_mm):
            continue
        # Place label at bottom edge, centered
        label_x = x - 2.5 * label_scale_mm * len(f"{int(round(x))}")  # Approximate centering
        label_y = box.min_y - 12 * label_scale_mm  # Below grid
        verts = _number_to_digit_vertices(x, label_x, label_y, z, label_scale_mm, label_color)
        if verts.size > 0:
            all_vertices.append(verts)

    if not all_vertices:
        return np.empty((0, _LINE_STRIDE), dtype=np.float32)
    return np.concatenate(all_vertices, axis=0)


def _centered_steps(center: float, lo: float, hi: float, step: float) -> list[float]:
    """Return step-aligned coordinates centred on ``center`` within ``[lo, hi]``."""
    if step <= 0:
        return []
    values = [center]
    index = 1
    value = center + step
    while value <= hi + 1e-9:
        values.append(value)
        index += 1
        value = center + index * step
    index = 1
    value = center - step
    while value >= lo - 1e-9:
        values.append(value)
        index += 1
        value = center - index * step
    return sorted(values)


def _is_major(center: float, value: float, major_spacing_mm: float) -> bool:
    """True when ``value`` is a major-grid multiple of ``center``."""
    if major_spacing_mm <= 0:
        return False
    offset = value - center
    nearest = round(offset / major_spacing_mm)
    return abs(offset - nearest * major_spacing_mm) <= max(1e-6, major_spacing_mm * 1e-3)


def axes_vertices(length_mm: float = 20.0) -> np.ndarray:
    """Build line vertices for unit axes at origin (X=red, Y=green, Z=blue).

    Each axis is a line from origin to ``length_mm`` along that axis,
    with an arrow head (two short lines at the tip).
    """
    # Axis lines (origin to tip)
    vertices = [
        # X axis (red)
        (0.0, 0.0, 0.0),
        (length_mm, 0.0, 0.0),
        # X arrow head
        (length_mm, 0.0, 0.0),
        (length_mm - 2.0, 1.0, 0.0),
        (length_mm, 0.0, 0.0),
        (length_mm - 2.0, -1.0, 0.0),
        (length_mm, 0.0, 0.0),
        (length_mm - 2.0, 0.0, 1.0),
        (length_mm, 0.0, 0.0),
        (length_mm - 2.0, 0.0, -1.0),
        # Y axis (green)
        (0.0, 0.0, 0.0),
        (0.0, length_mm, 0.0),
        # Y arrow head
        (0.0, length_mm, 0.0),
        (1.0, length_mm - 2.0, 0.0),
        (0.0, length_mm, 0.0),
        (-1.0, length_mm - 2.0, 0.0),
        (0.0, length_mm, 0.0),
        (0.0, length_mm - 2.0, 1.0),
        (0.0, length_mm, 0.0),
        (0.0, length_mm - 2.0, -1.0),
        # Z axis (blue)
        (0.0, 0.0, 0.0),
        (0.0, 0.0, length_mm),
        # Z arrow head
        (0.0, 0.0, length_mm),
        (1.0, 0.0, length_mm - 2.0),
        (0.0, 0.0, length_mm),
        (-1.0, 0.0, length_mm - 2.0),
        (0.0, 0.0, length_mm),
        (0.0, 1.0, length_mm - 2.0),
        (0.0, 0.0, length_mm),
        (0.0, -1.0, length_mm - 2.0),
    ]
    arr = np.array(vertices, dtype=np.float32)  # (N, 3)
    buffer = np.empty((len(arr), _LINE_STRIDE), dtype=np.float32)
    buffer[:, 0:3] = arr

    # Colors: X=red, Y=green, Z=blue (each vertex gets the axis color)
    # Each axis has 10 vertices (5 line segments * 2 endpoints)
    colors = []
    # X axis (10 vertices = 5 segments)
    for _ in range(10):
        colors.append((1.0, 0.0, 0.0, 1.0))
    # Y axis (10 vertices)
    for _ in range(10):
        colors.append((0.0, 1.0, 0.0, 1.0))
    # Z axis (10 vertices)
    for _ in range(10):
        colors.append((0.0, 0.0, 1.0, 1.0))
    buffer[:, 3:7] = np.array(colors, dtype=np.float32)
    return buffer


def ground_vertices(box, *, margin: float | None = None) -> np.ndarray:
    """Build a ground quad (two triangles) on the ``z = box.min_z`` plane.

    The quad is a square centered on the box and sized to fade out beyond the
    work area; the renderer draws it as a soft blended floor under the scene.
    """
    if box.is_empty:
        return np.empty((0, 3), dtype=np.float32)
    if margin is None:
        margin = max(box.width, box.height, box.depth) * 0.35
    half = max(box.width, box.height) / 2.0 + margin
    if half <= 1e-3:
        return np.empty((0, 3), dtype=np.float32)
    center_x = (box.min_x + box.max_x) / 2.0
    center_y = (box.min_y + box.max_y) / 2.0
    z = box.min_z
    corners = np.array(
        [
            [center_x - half, center_y - half, z],
            [center_x + half, center_y - half, z],
            [center_x + half, center_y + half, z],
            [center_x - half, center_y + half, z],
        ],
        dtype=np.float32,
    )
    return np.array(
        [corners[0], corners[1], corners[2], corners[0], corners[2], corners[3]],
        dtype=np.float32,
    )


def _line_segments(node: RenderNode) -> np.ndarray:
    """Return the line segments of a node as an ``(n, 2, 3)`` array.

    Strips are converted with vectorized slicing (O(1) numpy ops per node)
    instead of a per-segment Python loop.
    """
    if node.kind is NodeKind.GRID:
        if node.box is None or node.spacing_mm is None:
            return np.empty((0, 2, 3), dtype=np.float32)
        positions = grid_vertices(node.box, node.spacing_mm)[:, 0:3]
        return positions.reshape(-1, 2, 3)
    points = node.points
    if len(points) < 6:
        return np.empty((0, 2, 3), dtype=np.float32)
    array = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    if len(array) < 2:
        return np.empty((0, 2, 3), dtype=np.float32)
    starts = array[:-1]
    ends = array[1:]
    if node.kind in {NodeKind.CLOSED_POLYLINE, NodeKind.CIRCLE_OUTLINE}:
        ends = np.concatenate([ends, array[:1]])
        starts = np.concatenate([starts, array[-1:]]) if len(array) > 1 else starts
    return np.stack([starts, ends], axis=1)


def _box_vertices(node: RenderNode) -> np.ndarray:
    """36-vertex (12 triangle) expansion of a solid box with normals."""
    assert node.box is not None
    box = node.box
    min_corner = np.array([box.min_x, box.min_y, box.min_z], dtype=np.float32)
    max_corner = np.array([box.max_x, box.max_y, box.max_z], dtype=np.float32)
    corners = np.array(
        [
            [min_corner[0], min_corner[1], min_corner[2]],
            [max_corner[0], min_corner[1], min_corner[2]],
            [max_corner[0], max_corner[1], min_corner[2]],
            [min_corner[0], max_corner[1], min_corner[2]],
            [min_corner[0], min_corner[1], max_corner[2]],
            [max_corner[0], min_corner[1], max_corner[2]],
            [max_corner[0], max_corner[1], max_corner[2]],
            [min_corner[0], max_corner[1], max_corner[2]],
        ],
        dtype=np.float32,
    )
    # Face quads (corner indices) with outward normals: (+X, -X, +Y, -Y, +Z, -Z)
    faces = [
        (1, 2, 6, 5, (1, 0, 0)),
        (0, 4, 7, 3, (-1, 0, 0)),
        (3, 7, 6, 2, (0, 1, 0)),
        (0, 1, 5, 4, (0, -1, 0)),
        (4, 5, 6, 7, (0, 0, 1)),
        (0, 3, 2, 1, (0, 0, -1)),
    ]
    color = np.asarray(node.color, dtype=np.float32)
    blocks: list[np.ndarray] = []
    for a, b, c, d, normal in faces:
        quad = corners[[a, b, c, d]]
        block = np.empty((6, _SOLID_STRIDE), dtype=np.float32)
        block[0:3, 0:3] = quad[[0, 1, 2]]
        block[3:6, 0:3] = quad[[0, 2, 3]]
        block[:, 3:6] = np.asarray(normal, dtype=np.float32)
        block[:, 6:10] = color
        blocks.append(block)
    return np.concatenate(blocks, axis=0)


def _pick_rgba(picking_id: int) -> tuple[float, float, float, float]:
    """Encode a picking id as RGBA floats in ``[0, 1]`` (24-bit space)."""
    red = (picking_id >> 16) & 0xFF
    green = (picking_id >> 8) & 0xFF
    blue = picking_id & 0xFF
    return (red / 255.0, green / 255.0, blue / 255.0, 1.0)


def decode_picking_id(rgba: tuple[float, float, float, float]) -> int:
    """Decode an RGBA pixel (floats in ``[0, 1]``) back to a picking id."""
    red = round(rgba[0] * 255.0) & 0xFF
    green = round(rgba[1] * 255.0) & 0xFF
    blue = round(rgba[2] * 255.0) & 0xFF
    return (red << 16) | (green << 8) | blue


def mesh_vertices(vertices: np.ndarray, triangles: np.ndarray, color: tuple) -> np.ndarray:
    """Expand a triangle mesh into solid-pipeline vertices with face normals.

    ``vertices`` is ``(N, 3)``, ``triangles`` is ``(M, 3)``; the result is an
    ``(M * 3, 10)`` interleaved array (position, normal, color).
    """
    if len(triangles) == 0:
        return np.empty((0, _SOLID_STRIDE), dtype=np.float32)
    corners = vertices[triangles]  # (M, 3, 3)
    v0 = corners[:, 0]
    v1 = corners[:, 1]
    v2 = corners[:, 2]
    normals = np.cross(v1 - v0, v2 - v0)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 1e-12)
    buffer = np.empty((len(triangles) * 3, _SOLID_STRIDE), dtype=np.float32)
    buffer[:, 0:3] = corners.reshape(-1, 3)
    buffer[:, 3:6] = np.repeat(normals, 3, axis=0)
    buffer[:, 6:10] = np.asarray(color, dtype=np.float32)
    return buffer


def unit_box_mesh(size_mm: float, color: tuple) -> tuple[np.ndarray, np.ndarray]:
    """A unit axis-aligned box mesh (vertices + triangles) for tool markers."""
    half = size_mm / 2.0
    corners = np.array(
        [
            [-half, -half, -half],
            [half, -half, -half],
            [half, half, -half],
            [-half, half, -half],
            [-half, -half, half],
            [half, -half, half],
            [half, half, half],
            [-half, half, half],
        ],
        dtype=np.float64,
    )
    faces = [
        (1, 2, 6, 5),
        (0, 4, 7, 3),
        (3, 7, 6, 2),
        (0, 1, 5, 4),
        (4, 5, 6, 7),
        (0, 3, 2, 1),
    ]
    triangles = np.array(
        [[face[0], face[1], face[2]] for face in faces] + [[face[0], face[2], face[3]] for face in faces],
        dtype=np.int64,
    )
    return corners, triangles


__all__ = [
    "decode_picking_id",
    "grid_vertices",
    "grid_label_vertices",
    "ground_vertices",
    "line_vertices",
    "mesh_vertices",
    "solid_vertices",
    "unit_box_mesh",
    "axes_vertices",
]
