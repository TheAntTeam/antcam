"""Deterministic builders: domain contracts -> neutral render scene graphs.

Every builder is pure and stable: the same input always produces the same
graph (canonical order, tolerance-aware sampling), so frontends can cache on
``RenderScene.fingerprint()`` and tests can assert exact outputs.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path as PathLib

from antcam_rc2.core.databases.models import MachineProfile
from antcam_rc2.core.geometry.curves import Arc, Circle, Curve2, LineSegment
from antcam_rc2.core.geometry.paths import Contour, Path
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.geometry3d.scene import SolidScene
from antcam_rc2.core.io.scene import GeometryScene
from antcam_rc2.core.io3d import import_file_3d
from antcam_rc2.core.project.models import FixtureKind, OperationType, Project
from antcam_rc2.core.rendering.scene_graph import (
    RGBA,
    NodeKind,
    PickEntry,
    RenderBox,
    RenderMesh,
    RenderNode,
    RenderScene,
    flat_points,
)
from antcam_rc2.core.toolpath.models import MotionCommand, MotionKind, ToolpathPlan

_LAYER_PALETTE: tuple[RGBA, ...] = (
    (0.35, 0.64, 0.94, 1.0),  # blue
    (0.94, 0.60, 0.29, 1.0),  # orange
    (0.42, 0.78, 0.51, 1.0),  # green
    (0.91, 0.36, 0.46, 1.0),  # red
    (0.71, 0.57, 0.91, 1.0),  # purple
    (0.29, 0.76, 0.83, 1.0),  # teal
    (0.93, 0.82, 0.35, 1.0),  # yellow
    (0.87, 0.53, 0.79, 1.0),  # pink
)

_CIRCLE_SEGMENTS = 64
_MIN_CIRCLE_SEGMENTS = 16
_MAX_CIRCLE_SEGMENTS = 64
_ARC_SEGMENTS_PER_PI = 16
# Display-only sampling tolerance: the neutral graph is a visual description,
# so 0.02 mm chord error is plenty; the source geometry keeps full precision.
_RENDER_TOLERANCE_MM = 0.02


def _stock_corner_bounds(project: Project) -> tuple[float, float, float, float, float, float]:
    """Return stock corner bounds (min_x, min_y, min_z, max_x, max_y, max_z) respecting Stock.origin.

    The Stock.position represents different reference points depending on Stock.origin:
    - CENTER_XY_TOP_Z: position = center XY, top Z
    - CORNER_XY_TOP_Z: position = corner XY, top Z
    - CENTER_XY_ZERO_Z: position = center XY, zero Z
    - CORNER_XY_ZERO_Z: position = corner XY, zero Z
    """
    stock = project.stock
    offset_x = project.wcs.offset_x_mm
    offset_y = project.wcs.offset_y_mm
    offset_z = project.wcs.offset_z_mm
    w = stock.width_mm
    l = stock.length_mm
    h = stock.height_mm
    px = stock.position_x_mm + offset_x
    py = stock.position_y_mm + offset_y
    pz = stock.position_z_mm + offset_z

    if stock.origin.value == "center_xy_top_z":
        # Position is center XY, top Z
        min_x = px - w / 2.0
        min_y = py - l / 2.0
        min_z = pz - h
        max_x = px + w / 2.0
        max_y = py + l / 2.0
        max_z = pz
    elif stock.origin.value == "corner_xy_top_z":
        # Position is corner XY, top Z
        min_x = px
        min_y = py
        min_z = pz - h
        max_x = px + w
        max_y = py + l
        max_z = pz
    elif stock.origin.value == "center_xy_zero_z":
        # Position is center XY, zero Z
        min_x = px - w / 2.0
        min_y = py - l / 2.0
        min_z = pz
        max_x = px + w / 2.0
        max_y = py + l / 2.0
        max_z = pz + h
    else:  # corner_xy_zero_z
        # Position is corner XY, zero Z
        min_x = px
        min_y = py
        min_z = pz
        max_x = px + w
        max_y = py + l
        max_z = pz + h

    return (min_x, min_y, min_z, max_x, max_y, max_z)


def _stock_center_xy(project: Project) -> tuple[float, float]:
    """Return stock center XY respecting Stock.origin."""
    stock = project.stock
    offset_x = project.wcs.offset_x_mm
    offset_y = project.wcs.offset_y_mm
    w = stock.width_mm
    l = stock.length_mm
    px = stock.position_x_mm + offset_x
    py = stock.position_y_mm + offset_y

    if stock.origin.value in ("center_xy_top_z", "center_xy_zero_z"):
        # Position is center XY
        return (px, py)
    else:
        # Position is corner XY
        return (px + w / 2.0, py + l / 2.0)


def _stock_origin_corner(project: Project) -> tuple[float, float, float]:
    """Return the stock origin corner (where the red triad should be drawn) respecting Stock.origin.

    The origin corner is the corner that represents the stock's origin as defined by Stock.origin:
    - CENTER_XY_TOP_Z: position=center XY, top Z → origin at (-w/2, -l/2, pz + h/2)
    - CORNER_XY_TOP_Z: position=corner XY, top Z → origin at (px, py, pz)
    - CENTER_XY_ZERO_Z: position=center XY, zero Z → origin at (-w/2, -l/2, pz - h/2)
    - CORNER_XY_ZERO_Z: position=corner XY, zero Z → origin at (px, py, pz)
    """
    stock = project.stock
    offset_x = project.wcs.offset_x_mm
    offset_y = project.wcs.offset_y_mm
    offset_z = project.wcs.offset_z_mm
    w = stock.width_mm
    l = stock.length_mm
    h = stock.height_mm
    px = stock.position_x_mm + offset_x
    py = stock.position_y_mm + offset_y
    pz = stock.position_z_mm + offset_z

    if stock.origin.value in ("center_xy_top_z", "corner_xy_top_z"):
        # Origin is at TOP in Z (max_z)
        if stock.origin.value in ("center_xy_top_z", "center_xy_zero_z"):
            # Position is center XY
            ox = px - w / 2.0
            oy = py - l / 2.0
        else:
            # Position is corner XY
            ox = px
            oy = py
        # For TOP_Z variants, origin is at top of stock
        if stock.origin.value == "center_xy_top_z":
            oz = pz + h / 2.0
        else:  # corner_xy_top_z
            oz = pz
    else:  # zero_z variants
        # Origin is at BOTTOM in Z (min_z)
        if stock.origin.value in ("center_xy_zero_z", "corner_xy_zero_z"):
            if stock.origin.value == "center_xy_zero_z":
                # Position is center XY, zero Z
                ox = px - w / 2.0
                oy = py - l / 2.0
            else:  # corner_xy_zero_z
                ox = px
                oy = py
            oz = pz - h / 2.0 if stock.origin.value == "center_xy_zero_z" else pz

    return (ox, oy, oz)


@lru_cache(maxsize=32)
def _load_fixture_mesh(mesh_path: str, mtime: float) -> tuple[tuple[float, ...], tuple[int, ...]] | None:
    """Load and cache fixture mesh data (vertices, triangles) from a STEP/STL file.

    Cache is keyed by absolute path and modification time to auto-invalidate on file changes.
    Returns (vertices, triangles) as flat tuples, or None if loading fails.
    """
    try:
        mesh_scene = import_file_3d(mesh_path)
        mesh_graph = solid_to_scene(mesh_scene)
        if not mesh_graph.meshes:
            return None
        # Combine all meshes into one (fixtures are typically single mesh)
        all_vertices = []
        all_triangles = []
        vertex_offset = 0
        for mesh in mesh_graph.meshes:
            all_vertices.extend(mesh.vertices)
            # Adjust triangle indices
            all_triangles.extend(idx + vertex_offset for idx in mesh.triangles)
            vertex_offset += len(mesh.vertices) // 3
        return (tuple(all_vertices), tuple(all_triangles))
    except Exception:
        return None


def default_layer_color(layer: str) -> RGBA:
    """Deterministic per-layer color derived from the layer name."""
    index = sum(ord(character) for character in layer) % len(_LAYER_PALETTE)
    return _LAYER_PALETTE[index]


def geometry_to_scene(
    scene: GeometryScene,
    *,
    color_for_layer: Callable[[str], RGBA] = default_layer_color,
    width_px: float = 2.0,
) -> RenderScene:
    """Build a render graph for an imported :class:`GeometryScene`.

    Entities are emitted in layer/insertion order (the same order used by
    ``create_geometry_ref``), so the picking index maps directly back to
    ``(layer, entity_index)``.  A multi-contour ``Path`` shares one picking id.
    """
    nodes: list[RenderNode] = []
    picking: list[PickEntry] = []
    next_id = 1
    for layer in scene.layers:
        color = color_for_layer(layer.name)
        for entity_index, entity in enumerate(layer.entities):
            kind, points = _entity_shape(entity, scene.tolerance_mm)
            if points is None:
                continue
            nodes.append(
                RenderNode(
                    kind=kind,
                    points=points,
                    color=color,
                    width_px=width_px,
                    picking_id=next_id,
                    layer=layer.name,
                )
            )
            picking.append(PickEntry(picking_id=next_id, kind="geometry", layer=layer.name, entity_index=entity_index))
            next_id += 1
    return RenderScene(nodes=tuple(nodes), picking=tuple(picking))


def setup_to_scene(
    project: Project,
    machine: MachineProfile | None = None,
    *,
    stock_color: RGBA = (0.30, 0.52, 0.82, 0.35),
    stock_edge_color: RGBA | None = None,
    fixture_color: RGBA = (0.7, 0.15, 0.15, 1.0),
    fixture_outline_color: RGBA = (0.9, 0.3, 0.3, 1.0),
    fixture_mesh_color: RGBA = (0.65, 0.1, 0.1, 1.0),
    work_area_color: RGBA = (0.35, 0.37, 0.42, 1.0),
    origin_color: RGBA = (0.91, 0.29, 0.29, 1.0),
    fixture_library_base: PathLib | None = None,
) -> RenderScene:
    """Build the setup graph: stock, fixtures, machine work area and WCS origin.

    Fixtures with a valid ``mesh_path`` are rendered as meshes (translated to
    the fixture position); fixtures without a mesh fall back to a solid box
    with the given ``fixture_color``. If ``fixture_library_base`` is provided,
    relative mesh paths are resolved against it.
    """
    import logging

    logger = logging.getLogger(__name__)

    logger.info("=== SETUP_TO_SCENE START ===")
    logger.info(f"Project: {project.name} (id={project.id})")
    logger.info(f"Machine: {machine.id if machine else 'None'}")
    logger.info(f"WCS offsets: x={project.wcs.offset_x_mm}, y={project.wcs.offset_y_mm}, z={project.wcs.offset_z_mm}")

    stock = project.stock
    logger.info("--- STOCK DATA FROM CORE ---")
    logger.info(f"  Stock ID: {stock.material_id}")
    logger.info(f"  Position (raw): x={stock.position_x_mm}, y={stock.position_y_mm}, z={stock.position_z_mm}")
    logger.info(f"  Dimensions: width={stock.width_mm}, length={stock.length_mm}, height={stock.height_mm}")
    logger.info(f"  Origin: {stock.origin}")

    nodes: list[RenderNode] = []
    meshes: list[RenderMesh] = []
    offset_x = project.wcs.offset_x_mm
    offset_y = project.wcs.offset_y_mm
    offset_z = project.wcs.offset_z_mm

    # Compute stock bounds respecting Stock.origin
    min_x, min_y, min_z, max_x, max_y, max_z = _stock_corner_bounds(project)
    stock_box = RenderBox(
        min_x=min_x,
        min_y=min_y,
        min_z=min_z,
        max_x=max_x,
        max_y=max_y,
        max_z=max_z,
    )
    logger.info("--- STOCK RENDERBOX CALCULATED ---")
    logger.info(f"  min: ({stock_box.min_x:.3f}, {stock_box.min_y:.3f}, {stock_box.min_z:.3f})")
    logger.info(f"  max: ({stock_box.max_x:.3f}, {stock_box.max_y:.3f}, {stock_box.max_z:.3f})")
    logger.info(f"  center: ({stock_box.center[0]:.3f}, {stock_box.center[1]:.3f}, {stock_box.center[2]:.3f})")
    logger.info(f"  size: ({stock_box.width:.3f}, {stock_box.height:.3f}, {stock_box.depth:.3f})")
    logger.info(f"  is_empty: {stock_box.is_empty}")

    logger.info("--- CREATING STOCK SOLID_BOX NODE ---")
    logger.info("  NodeKind: SOLID_BOX")
    logger.info(f"  Color: {stock_color}")
    nodes.append(RenderNode(kind=NodeKind.SOLID_BOX, box=stock_box, color=stock_color))
    logger.info("  Node created, picking_id=0")

    logger.info("--- CREATING STOCK BOX_OUTLINE NODE ---")
    edge_color = stock_edge_color or _opaque(stock_color)
    logger.info("  NodeKind: BOX_OUTLINE")
    logger.info(f"  Color: {edge_color}")
    logger.info("  Width: 1.5px")
    nodes.append(
        RenderNode(
            kind=NodeKind.BOX_OUTLINE,
            box=stock_box,
            color=edge_color,
            width_px=1.5,
        )
    )
    logger.info("  Node created, picking_id=0")

    logger.info(f"--- PROCESSING FIXTURES ({len(project.fixtures)} fixtures) ---")
    for fixture in project.fixtures:
        logger.info(f"  Fixture: {fixture.name} (id={fixture.id}, kind={fixture.kind})")
        logger.info(
            f"    Position (raw): x={fixture.position_x_mm}, y={fixture.position_y_mm}, z={fixture.position_z_mm}"
        )
        logger.info(f"    Dimensions: w={fixture.width_mm}, l={fixture.length_mm}, h={fixture.height_mm}")
        if fixture.kind == FixtureKind.SCREW:
            # Render screw as cylinder
            radius = fixture.screw_diameter_mm / 2.0 if fixture.screw_diameter_mm else 3.0
            height = fixture.screw_length_mm if fixture.screw_length_mm else 20.0
            center_x = fixture.position_x_mm + offset_x
            center_y = fixture.position_y_mm + offset_y
            base_z = fixture.position_z_mm + offset_z
            # Create cylinder as a mesh approximation (solid cylinder)
            cylinder_mesh = _create_cylinder_mesh(radius, height, 32)
            translated_vertices = _translate_vertices(
                cylinder_mesh.vertices,
                center_x,
                center_y,
                base_z,
            )
            meshes.append(
                RenderMesh(
                    vertices=translated_vertices,
                    triangles=cylinder_mesh.triangles,
                    color=fixture_mesh_color,
                    picking_id=fixture.id.__hash__() & 0xFFFFFFFF,
                )
            )
            logger.info(f"    Created SCREW fixture mesh: radius={radius}, height={height}, color={fixture_mesh_color}")
            # Also add a small cylinder for the hole (different color)
            if fixture.hole_diameter_mm:
                hole_radius = fixture.hole_diameter_mm / 2.0
                hole_mesh = _create_cylinder_mesh(hole_radius, height + 2.0, 32)
                translated_hole = _translate_vertices(
                    hole_mesh.vertices,
                    center_x,
                    center_y,
                    base_z - 1.0,  # Slightly below to show hole
                )
                meshes.append(
                    RenderMesh(
                        vertices=translated_hole,
                        triangles=hole_mesh.triangles,
                        color=(0.2, 0.2, 0.2, 1.0),  # Dark color for hole
                        picking_id=0,
                    )
                )
                logger.info(f"    Created SCREW hole mesh: radius={hole_radius}, color=(0.2, 0.2, 0.2, 1.0)")
        else:
            # Regular box fixture
            fixture_box = RenderBox(
                min_x=fixture.position_x_mm + offset_x,
                min_y=fixture.position_y_mm + offset_y,
                min_z=fixture.position_z_mm + offset_z,
                max_x=fixture.position_x_mm + offset_x + fixture.width_mm,
                max_y=fixture.position_y_mm + offset_y + fixture.length_mm,
                max_z=fixture.position_z_mm + offset_z + fixture.height_mm,
            )
            logger.info(
                f"    Fixture RenderBox: min=({fixture_box.min_x:.3f}, {fixture_box.min_y:.3f}, {fixture_box.min_z:.3f}) max=({fixture_box.max_x:.3f}, {fixture_box.max_y:.3f}, {fixture_box.max_z:.3f})"
            )
            if fixture.mesh_path:
                mesh_path = PathLib(fixture.mesh_path)
                # Resolve relative paths against fixture library base
                if not mesh_path.is_absolute() and fixture_library_base is not None:
                    mesh_path = fixture_library_base / mesh_path
                if mesh_path.exists():
                    try:
                        mtime = mesh_path.stat().st_mtime
                        cached = _load_fixture_mesh(str(mesh_path.resolve()), mtime)
                        if cached is not None:
                            vertices, triangles = cached
                            translated_vertices = _translate_vertices(
                                vertices,
                                fixture.position_x_mm + offset_x,
                                fixture.position_y_mm + offset_y,
                                fixture.position_z_mm + offset_z,
                            )
                            meshes.append(
                                RenderMesh(
                                    vertices=translated_vertices,
                                    triangles=triangles,
                                    color=fixture_mesh_color,
                                    picking_id=abs(hash(str(mesh_path))) & 0xFFFFFFFF,
                                )
                            )
                            logger.info(
                                f"    Created FIXTURE mesh from file: {mesh_path}, vertices={len(vertices) // 3}, triangles={len(triangles) // 3}, color={fixture_mesh_color}"
                            )
                            continue  # Skip box fallback
                    except Exception as e:
                        # Fall through to box rendering on import failure
                        logger.warning(f"    Failed to load fixture mesh from {mesh_path}: {e}, falling back to box")
                        pass
            # Box fallback (or mesh import failed)
            logger.info(
                f"    Creating FIXTURE box fallback: SOLID_BOX color={fixture_color}, BOX_OUTLINE color={fixture_outline_color}, width=1.0px"
            )
            nodes.append(RenderNode(kind=NodeKind.SOLID_BOX, box=fixture_box, color=fixture_color))
            nodes.append(
                RenderNode(
                    kind=NodeKind.BOX_OUTLINE,
                    box=fixture_box,
                    color=fixture_outline_color,
                    width_px=1.0,
                )
            )

    logger.info("--- PROCESSING MACHINE WORK AREA ---")
    if machine is not None:
        # Work area is defined in machine coordinates, offset by WCS
        work_area = RenderBox(
            min_x=offset_x,
            min_y=offset_y,
            min_z=offset_z,
            max_x=offset_x + machine.work_area_x_mm,
            max_y=offset_y + machine.work_area_y_mm,
            max_z=offset_z + machine.work_area_z_mm,
        )
        logger.info(f"  Machine: {machine.id}")
        logger.info(
            f"  Work area: min=({offset_x:.3f},{offset_y:.3f},{offset_z:.3f}) max=({offset_x + machine.work_area_x_mm:.3f},{offset_y + machine.work_area_y_mm:.3f},{offset_z + machine.work_area_z_mm:.3f})"
        )
        logger.info(f"  Creating WORK_AREA BOX_OUTLINE: color={work_area_color}, width=1.0px")
        nodes.append(RenderNode(kind=NodeKind.BOX_OUTLINE, box=work_area, color=work_area_color, width_px=1.0))
    else:
        logger.info("  No machine selected, skipping work area")

    # Origin axes at stock origin corner (respecting Stock.origin)
    axis_length = max(5.0, min(stock.width_mm, stock.length_mm, stock.height_mm) * 0.25)
    origin_x, origin_y, origin_z = _stock_origin_corner(project)
    logger.info("--- CREATING ORIGIN AXES ---")
    logger.info(f"  Axis length: {axis_length:.3f} (25% of min stock dimension, min 5mm)")
    logger.info(f"  Origin (stock origin corner): ({origin_x:.3f}, {origin_y:.3f}, {origin_z:.3f})")
    logger.info(f"  Origin axes color: {origin_color}")
    for axis in range(3):
        direction = [0.0, 0.0, 0.0]
        direction[axis] = axis_length
        axis_name = ["X", "Y", "Z"][axis]
        logger.info(
            f"  Axis {axis_name}: ({origin_x:.3f}, {origin_y:.3f}, {origin_z:.3f}) -> ({origin_x + direction[0]:.3f}, {origin_y + direction[1]:.3f}, {origin_z + direction[2]:.3f})"
        )
        nodes.append(
            RenderNode(
                kind=NodeKind.LINE_STRIP,
                points=flat_points(
                    [
                        (origin_x, origin_y, origin_z),
                        (origin_x + direction[0], origin_y + direction[1], origin_z + direction[2]),
                    ]
                ),
                color=origin_color,
                width_px=2.0,
            )
        )

    logger.info(f"=== SETUP_TO_SCENE END: {len(nodes)} nodes, {len(meshes)} meshes ===")
    return RenderScene(nodes=tuple(nodes), meshes=tuple(meshes))


def toolpath_to_scene(
    plan: ToolpathPlan,
    *,
    color_for_operation: Callable[[OperationType], RGBA],
    rapid_color: RGBA = (0.62, 0.64, 0.68, 1.0),
    cut_width_px: float = 2.5,
    rapid_width_px: float = 1.0,
) -> RenderScene:
    """Build the toolpath graph: one pickable strip per operation.

    Rapid links and cutting moves are emitted as separate strips so a renderer
    can dim rapids; arcs are flattened deterministically for the GPU.
    """
    nodes: list[RenderNode] = []
    picking: list[PickEntry] = []
    next_id = 1
    for result in plan.operations:
        if result.program is None:
            continue
        operation_id = result.operation_id
        cut_color = color_for_operation(result.operation_type)
        cuts = _collect_strips(result.program.motions, is_cut=True)
        rapids = _collect_strips(result.program.motions, is_cut=False)
        for strip in cuts:
            nodes.append(
                RenderNode(
                    kind=NodeKind.TOOLPATH_STRIP,
                    points=flat_points(strip.points),
                    color=cut_color,
                    width_px=cut_width_px,
                    picking_id=next_id,
                    operation_id=operation_id,
                    pass_index=strip.pass_index,
                )
            )
            picking.append(PickEntry(picking_id=next_id, kind="operation", operation_id=operation_id))
            next_id += 1
        for strip in rapids:
            nodes.append(
                RenderNode(
                    kind=NodeKind.TOOLPATH_STRIP,
                    points=flat_points(strip.points),
                    color=rapid_color,
                    width_px=rapid_width_px,
                    operation_id=operation_id,
                    pass_index=strip.pass_index,
                )
            )
    return RenderScene(nodes=tuple(nodes), picking=tuple(picking))


class _Strip:
    __slots__ = ("points", "pass_index")

    def __init__(self, pass_index: int | None) -> None:
        self.points: list[tuple[float, float, float]] = []
        self.pass_index = pass_index


def _collect_strips(motions: tuple[MotionCommand, ...], *, is_cut: bool) -> list[_Strip]:
    """Group consecutive same-kind, same-pass motions into line strips.

    A strip is a maximal run of motions of the requested kind; single-point
    runs (isolated rapids) are dropped as invisible.  Arcs are flattened.
    """
    strips: list[_Strip] = []
    current: _Strip | None = None
    for motion in motions:
        if motion.kind is MotionKind.DWELL:
            continue
        motion_is_cut = motion.kind in {MotionKind.CUT_LINEAR, MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW}
        if motion_is_cut != is_cut:
            _finalize_strip(strips, current)
            current = None
            continue
        if current is None or current.pass_index != motion.pass_index:
            _finalize_strip(strips, current)
            current = _Strip(motion.pass_index)
        if motion.kind in {MotionKind.CUT_ARC_CW, MotionKind.CUT_ARC_CCW} and current.points:
            current.points.extend(_flatten_arc(_last_point(current.points), motion))
        current.points.append((motion.endpoint.x_mm, motion.endpoint.y_mm, motion.endpoint.z_mm))
    _finalize_strip(strips, current)
    return strips


def _finalize_strip(strips: list[_Strip], current: _Strip | None) -> None:
    """Append ``current`` when it has enough points to be drawable."""
    if current is not None and len(current.points) >= 2:
        strips.append(current)


def _last_point(points: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    return points[-1]


def _flatten_arc(start: tuple[float, float, float], motion: MotionCommand) -> list[tuple[float, float, float]]:
    """Sample an arc motion into intermediate points (endpoint excluded)."""
    assert motion.arc_center_xy is not None
    start_x, start_y, start_z = start
    center_x, center_y = motion.arc_center_xy
    radius = math.hypot(start_x - center_x, start_y - center_y)
    start_angle = math.atan2(start_y - center_y, start_x - center_x)
    end_angle = math.atan2(motion.endpoint.y_mm - center_y, motion.endpoint.x_mm - center_x)
    if motion.kind is MotionKind.CUT_ARC_CCW:
        sweep = (end_angle - start_angle) % (2.0 * math.pi)
    else:
        sweep = -((start_angle - end_angle) % (2.0 * math.pi))
    if radius <= 1e-9:
        return []
    # A start == end arc is a full circle even though the angle difference is 0.
    if math.hypot(motion.endpoint.x_mm - start_x, motion.endpoint.y_mm - start_y) <= 1e-6:
        sweep = 2.0 * math.pi if motion.kind is MotionKind.CUT_ARC_CCW else -2.0 * math.pi
    segments = max(1, math.ceil(abs(sweep) * _ARC_SEGMENTS_PER_PI / math.pi))
    points: list[tuple[float, float, float]] = []
    for index in range(1, segments):
        angle = start_angle + sweep * index / segments
        points.append(
            (
                center_x + radius * math.cos(angle),
                center_y + radius * math.sin(angle),
                start_z + (motion.endpoint.z_mm - start_z) * index / segments,
            )
        )
    return points


def _entity_shape(entity, tolerance_mm: float) -> tuple[NodeKind, tuple[float, ...]] | tuple[None, None]:
    """Return the drawable shape for one scene entity (None when degenerate).

    ``tolerance_mm`` is ignored in favor of the fixed render tolerance: the
    graph is a visual description and display sampling must not depend on the
    (much finer) scene tolerance.
    """
    del tolerance_mm
    if isinstance(entity, Path):
        return _path_shape(entity, _RENDER_TOLERANCE_MM)
    if isinstance(entity, Circle):
        points = _circle_points(entity.center, entity.radius, _RENDER_TOLERANCE_MM)
        return (NodeKind.CIRCLE_OUTLINE, flat_points(points))
    if isinstance(entity, Contour):
        return _polyline_shape(entity.to_polyline(_RENDER_TOLERANCE_MM), closed=True)
    if isinstance(entity, (LineSegment, Arc, Curve2)):
        return _polyline_shape(entity.to_polyline(_RENDER_TOLERANCE_MM), closed=False)
    return (None, None)


def _path_shape(path: Path, tolerance_mm: float) -> tuple[NodeKind, tuple[float, ...]] | tuple[None, None]:
    """A Path is one logical entity: emit all contours under one picking id."""
    points: list[tuple[float, float, float]] = []
    for contour in path.contours:
        polyline = contour.to_polyline(tolerance_mm)
        points.extend((p.x, p.y, 0.0) for p in polyline)
        points.append((polyline[0].x, polyline[0].y, 0.0))  # close each loop
    if not points:
        return (None, None)
    return (NodeKind.CLOSED_POLYLINE, flat_points(points))


def _polyline_shape(polyline: list[Point2], *, closed: bool) -> tuple[NodeKind, tuple[float, ...]] | tuple[None, None]:
    if len(polyline) < 2:
        return (None, None)
    points = [(p.x, p.y, 0.0) for p in polyline]
    if closed and polyline[0] != polyline[-1]:
        points.append((polyline[0].x, polyline[0].y, 0.0))
    return (NodeKind.CLOSED_POLYLINE if closed else NodeKind.LINE_STRIP, flat_points(points))


@lru_cache(maxsize=8)
def _unit_circle(segments: int) -> tuple[tuple[float, float], ...]:
    """Unit-circle samples for a segment count (cos/sin computed once)."""
    return tuple(
        (math.cos(2.0 * math.pi * index / segments), math.sin(2.0 * math.pi * index / segments))
        for index in range(segments)
    )


def _circle_points(center: Point2, radius: float, tolerance_mm: float = 0.02) -> list[tuple[float, float, float]]:
    """Sample a circle with tolerance-driven segment count (bounded).

    Small circles need far fewer points than large ones; the chord error stays
    below ``tolerance_mm`` while the vertex count is capped for the GPU.  The
    unit samples are cached, so per-circle cost is a few multiplications.
    """
    if radius <= 1e-9:
        return []
    per_segment = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - tolerance_mm / radius)))
    segments = _MIN_CIRCLE_SEGMENTS
    if per_segment > 1e-9:
        segments = min(_MAX_CIRCLE_SEGMENTS, max(_MIN_CIRCLE_SEGMENTS, math.ceil(2.0 * math.pi / per_segment)))
    unit = _unit_circle(segments)
    points = [(center.x + radius * ux, center.y + radius * uy, 0.0) for ux, uy in unit]
    points.append(points[0])
    return points


def _translate_vertices(vertices: tuple[float, ...], dx: float, dy: float, dz: float) -> tuple[float, ...]:
    """Translate flat vertex tuple (x,y,z,x,y,z,...) by (dx,dy,dz)."""
    if not vertices:
        return vertices
    coords = list(vertices)
    for i in range(0, len(coords), 3):
        coords[i] += dx
        coords[i + 1] += dy
        coords[i + 2] += dz
    return tuple(coords)


def _create_cylinder_mesh(radius: float, height: float, segments: int) -> RenderMesh:
    """Create a solid cylinder mesh (triangulated)."""
    import math

    vertices = []
    triangles = []

    # Top center vertex
    vertices.extend([0.0, 0.0, height])
    top_center_idx = 0

    # Bottom center vertex
    vertices.extend([0.0, 0.0, 0.0])
    bottom_center_idx = 1

    # Generate circle vertices - separate top and bottom rings
    top_ring = []
    bottom_ring = []
    for i in range(segments):
        angle = 2.0 * math.pi * i / segments
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        # Top ring
        vertices.extend([x, y, height])
        top_ring.append(2 + i)
        # Bottom ring
        vertices.extend([x, y, 0.0])
        bottom_ring.append(2 + segments + i)

    # Top face triangles (fan from center)
    for i in range(segments):
        next_i = (i + 1) % segments
        triangles.extend([top_center_idx, top_ring[i], top_ring[next_i]])

    # Bottom face triangles (fan from center)
    for i in range(segments):
        next_i = (i + 1) % segments
        triangles.extend([bottom_center_idx, bottom_ring[next_i], bottom_ring[i]])

    # Side triangles (quad strips)
    for i in range(segments):
        next_i = (i + 1) % segments
        top_i = top_ring[i]
        top_next = top_ring[next_i]
        bottom_i = bottom_ring[i]
        bottom_next = bottom_ring[next_i]
        # Two triangles per quad
        triangles.extend([top_i, bottom_i, top_next])
        triangles.extend([top_next, bottom_i, bottom_next])

    return RenderMesh(
        vertices=tuple(vertices),
        triangles=tuple(triangles),
        color=(0.7, 0.7, 0.76, 1.0),  # Default color, will be overridden
        picking_id=0,
    )


def _opaque(color: RGBA) -> RGBA:
    return (color[0], color[1], color[2], 1.0)


def solid_to_scene(scene: SolidScene, placement: object | None = None) -> RenderScene:
    """Build a render graph with one :class:`RenderMesh` per solid body.

    If ``placement`` (SolidPlacement) is provided, the scene is transformed
    via ``apply_placement`` before flattening — the raw ``SolidScene`` stays
    immutable.
    """
    if placement is not None:
        # Lazy import to avoid circular dependency (models -> geometry3d).
        from antcam_rc2.core.project.solid_placement import apply_placement  # noqa: WPS433

        # Only apply if placement is a SolidPlacement; pass-through otherwise.
        try:
            from antcam_rc2.core.project.models import SolidPlacement  # noqa: WPS433

            if isinstance(placement, SolidPlacement):
                scene = apply_placement(scene, placement)
        except Exception:
            pass
    meshes: list[RenderMesh] = []
    for body_index, body in enumerate(scene.bodies):
        vertices = tuple(float(value) for value in body.mesh.vertices.reshape(-1).tolist())
        triangles = tuple(int(value) for value in body.mesh.faces.reshape(-1).tolist())
        meshes.append(
            RenderMesh(
                vertices=vertices,
                triangles=triangles,
                color=(0.72, 0.73, 0.76, 1.0),
                picking_id=body_index + 1,
            )
        )
    return RenderScene(meshes=tuple(meshes))


def compose_scenes(*scenes: RenderScene) -> RenderScene:
    """Concatenate render scenes into a single graph for one viewport."""
    return RenderScene(
        nodes=tuple(node for scene in scenes for node in scene.nodes),
        meshes=tuple(mesh for scene in scenes for mesh in scene.meshes),
        picking=tuple(entry for scene in scenes for entry in scene.picking),
    )


__all__ = [
    "compose_scenes",
    "default_layer_color",
    "geometry_to_scene",
    "setup_to_scene",
    "solid_to_scene",
    "toolpath_to_scene",
]
