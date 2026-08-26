"""Tests for the deterministic render graph builders."""

from __future__ import annotations

from antcam_rc2.core.geometry.curves import Circle, LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.project.models import Fixture, OperationType, Project, Stock
from antcam_rc2.core.rendering.builder import (
    default_layer_color,
    geometry_to_scene,
    setup_to_scene,
    toolpath_to_scene,
)
from antcam_rc2.core.rendering.scene_graph import NodeKind
from antcam_rc2.core.toolpath.models import (
    MotionCommand,
    MotionKind,
    MotionProgram,
    OperationPlanStatus,
    Position3,
    ToolpathOperationResult,
    ToolpathPlan,
)


def make_scene() -> GeometryScene:
    scene = GeometryScene(source=SourceInfo(format="dxf", path="fixture.dxf"))
    points = [Point2(0.0, 0.0), Point2(10.0, 0.0), Point2(10.0, 10.0), Point2(0.0, 10.0)]
    scene.add_entity(
        Contour([LineSegment(a, b) for a, b in zip(points, points[1:] + [points[0]], strict=True)]), "profile"
    )
    scene.add_entity(Circle(Point2(5.0, 5.0), 1.5), "holes")
    return scene


def test_default_layer_color_is_deterministic() -> None:
    assert default_layer_color("profile") == default_layer_color("profile")
    assert len(default_layer_color("profile")) == 4


def test_geometry_to_scene_maps_entities_to_pickable_nodes() -> None:
    graph = geometry_to_scene(make_scene())
    assert len(graph.nodes) == 2
    assert len(graph.picking) == 2

    profile_node = graph.nodes[0]
    assert profile_node.kind is NodeKind.CLOSED_POLYLINE
    assert profile_node.picking_id == 1
    assert profile_node.layer == "profile"
    assert graph.pick_entry(1).entity_index == 0
    assert graph.pick_entry(1).layer == "profile"

    circle_node = graph.nodes[1]
    assert circle_node.kind is NodeKind.CIRCLE_OUTLINE
    assert len(circle_node.points) % 3 == 0
    assert graph.pick_entry(2).entity_index == 0
    assert graph.pick_entry(2).layer == "holes"


def test_geometry_to_scene_ignores_empty_scene() -> None:
    graph = geometry_to_scene(GeometryScene(source=SourceInfo(format="dxf")))
    assert graph.nodes == ()
    assert graph.picking == ()


def test_setup_to_scene_builds_stock_fixture_work_area_and_origin() -> None:
    project = Project(
        id="proj_1234abcd",
        name="setup",
        created_at="2024-01-01T00:00:00Z",
        modified_at="2024-01-01T00:00:00Z",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
        fixtures=(Fixture(id="fix_1234abcd", name="vise", width_mm=10.0, length_mm=5.0, height_mm=3.0),),
    )
    graph = setup_to_scene(project, machine=None)

    kinds = [node.kind for node in graph.nodes]
    assert kinds.count(NodeKind.SOLID_BOX) == 2  # stock + fixture
    assert kinds.count(NodeKind.BOX_OUTLINE) == 2
    assert kinds.count(NodeKind.LINE_STRIP) == 3  # origin axes


def test_setup_to_scene_includes_work_area_with_machine(catalog_bundle) -> None:
    project = Project(
        id="proj_1234abcd",
        name="setup",
        created_at="2024-01-01T00:00:00Z",
        modified_at="2024-01-01T00:00:00Z",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    machine = catalog_bundle.machines["makera_z1"]
    graph = setup_to_scene(project, machine=machine)

    work_area = next(node for node in graph.nodes if node.box is not None and node.box.max_x == 200.0)
    assert work_area.kind is NodeKind.BOX_OUTLINE
    assert work_area.box.max_z == 100.0


def test_toolpath_to_scene_groups_cuts_and_rapids_per_operation() -> None:
    plan = ToolpathPlan(
        project_id="proj_1234abcd",
        operations=(
            ToolpathOperationResult(
                operation_id="op_1234abcd",
                operation_type=OperationType.PROFILING,
                status=OperationPlanStatus.SUCCEEDED,
                program=MotionProgram(
                    motions=(
                        MotionCommand(
                            kind=MotionKind.RAPID,
                            endpoint=Position3(x_mm=0.0, y_mm=0.0, z_mm=5.0),
                            operation_id="op_1234abcd",
                            pass_index=0,
                        ),
                        MotionCommand(
                            kind=MotionKind.RAPID,
                            endpoint=Position3(x_mm=10.0, y_mm=0.0, z_mm=5.0),
                            operation_id="op_1234abcd",
                            pass_index=0,
                        ),
                        MotionCommand(
                            kind=MotionKind.CUT_LINEAR,
                            endpoint=Position3(x_mm=10.0, y_mm=0.0, z_mm=0.0),
                            feed_mm_min=100.0,
                            operation_id="op_1234abcd",
                            pass_index=0,
                        ),
                        MotionCommand(
                            kind=MotionKind.CUT_LINEAR,
                            endpoint=Position3(x_mm=20.0, y_mm=0.0, z_mm=0.0),
                            feed_mm_min=100.0,
                            operation_id="op_1234abcd",
                            pass_index=0,
                        ),
                        MotionCommand(
                            kind=MotionKind.RAPID,
                            endpoint=Position3(x_mm=20.0, y_mm=0.0, z_mm=5.0),
                            operation_id="op_1234abcd",
                            pass_index=0,
                        ),
                        MotionCommand(
                            kind=MotionKind.RAPID,
                            endpoint=Position3(x_mm=0.0, y_mm=0.0, z_mm=5.0),
                            operation_id="op_1234abcd",
                            pass_index=0,
                        ),
                    )
                ),
            ),
        ),
    )
    graph = toolpath_to_scene(
        plan,
        color_for_operation=lambda op_type: (1.0, 0.6, 0.2, 1.0),
    )

    strips = [node for node in graph.nodes if node.kind is NodeKind.TOOLPATH_STRIP]
    assert len(strips) == 3  # one cut strip, two rapid links
    cut_strip = next(node for node in strips if node.color == (1.0, 0.6, 0.2, 1.0))
    assert cut_strip.operation_id == "op_1234abcd"
    assert cut_strip.pass_index == 0
    assert len(graph.picking) == 1  # only cut strips are pickable
    assert graph.pick_entry(1).operation_id == "op_1234abcd"
    assert any(node.color != (1.0, 0.6, 0.2, 1.0) for node in strips)  # rapids present


def test_toolpath_to_scene_flattens_arcs() -> None:
    plan = ToolpathPlan(
        project_id="proj_1234abcd",
        operations=(
            ToolpathOperationResult(
                operation_id="op_1234abcd",
                operation_type=OperationType.DRILL,
                status=OperationPlanStatus.SUCCEEDED,
                program=MotionProgram(
                    motions=(
                        MotionCommand(
                            kind=MotionKind.CUT_LINEAR,
                            endpoint=Position3(x_mm=5.0, y_mm=0.0, z_mm=0.0),
                            feed_mm_min=100.0,
                        ),
                        MotionCommand(
                            kind=MotionKind.CUT_ARC_CCW,
                            endpoint=Position3(x_mm=5.0, y_mm=0.0, z_mm=0.0),
                            feed_mm_min=100.0,
                            arc_center_xy=(5.0, 5.0),
                        ),
                    )
                ),
            ),
        ),
    )
    graph = toolpath_to_scene(plan, color_for_operation=lambda _: (1.0, 1.0, 1.0, 1.0))
    strip = next(node for node in graph.nodes if node.kind is NodeKind.TOOLPATH_STRIP)
    # full-circle arc flattened into intermediate points
    assert len(strip.points) >= 3 * 16


def test_render_builders_are_deterministic() -> None:
    scene = make_scene()
    first = geometry_to_scene(scene)
    second = geometry_to_scene(scene)
    assert first.fingerprint() == second.fingerprint()
