import math
import os
from types import SimpleNamespace

import numpy as np
import pytest

from antcam.contour_extractor import ContourExtractor
from antcam.feature_extractor import FeatureExtractor
from antcam.importer import Model
from antcam.path_generator import (
    AutoToolpathPlanner,
    MachiningParameterResolver,
    OperationRequest,
    ParameterOverrides,
    ToolDefinition,
    ToolManager,
)


def _hole(center, depth=8.0, through=False):
    return SimpleNamespace(
        type="hole",
        props={
            "center": center,
            "depth": depth,
            "through": through,
        },
    )


def _hole_group(centers, diameter=6.0, depth=8.0, through=False):
    holes = [_hole(center, depth=depth, through=through) for center in centers]
    return SimpleNamespace(
        type="hole_group",
        props={
            "diameter": diameter,
            "depth": depth,
            "through": through,
        },
        holes=holes,
    )


def _slot(center, axis=(1.0, 0.0, 0.0), width=6.0, length=18.0, depth=4.0, through=False):
    return SimpleNamespace(
        type="slot",
        props={
            "center": center,
            "axis": axis,
            "width": width,
            "length": length,
            "depth": depth,
            "through": through,
        },
    )


def _cavity(feature_type, center, span=(20.0, 12.0), depth=6.0, through=False):
    return SimpleNamespace(
        type=feature_type,
        props={
            "center": center,
            "span": span,
            "depth": depth,
            "through": through,
        },
        geometry=None,
    )


def _cavity_with_boundary(feature_type, boundary_loops, depth=6.0, through=False):
    return SimpleNamespace(
        type=feature_type,
        props={
            "boundary_loops": boundary_loops,
            "depth": depth,
            "through": through,
        },
        geometry=None,
    )


def test_generate_drilling_ops_from_hole_group():
    planner = AutoToolpathPlanner(working_plane_normal=(0.0, 0.0, 1.0), safe_z_offset=4.0, max_stepdown=20.0)
    features = [_hole_group([(10.0, 5.0, 0.0), (20.0, 5.0, 0.0)], through=False)]

    plan = planner.generate(features=features)

    assert len(plan.operations) == 1
    op = plan.operations[0]
    assert op.strategy == "drilling"
    assert op.metadata["hole_count"] == 2
    assert len(op.motions) == 8

    plunge_moves = [m for m in op.motions if m.move == "linear"]
    assert len(plunge_moves) == 2
    assert all(move.point[2] == -8.0 for move in plunge_moves)


def test_generate_profile_ops_from_polylines():
    planner = AutoToolpathPlanner(working_plane_normal=(0.0, 0.0, 1.0))
    square = [
        (0.0, 0.0, -2.0),
        (10.0, 0.0, -2.0),
        (10.0, 10.0, -2.0),
        (0.0, 10.0, -2.0),
    ]

    plan = planner.generate(features=[], perimeter_polylines=[square])

    assert len(plan.operations) == 1
    op = plan.operations[0]
    assert op.strategy == "2p5d_profile"
    assert op.metadata["point_count"] >= 4
    assert op.metadata["tool_radius_compensation"] == pytest.approx(3.0)
    assert op.metadata["cutter_side"] == "outside"
    assert op.motions[0].move == "rapid"
    assert any(m.move == "linear" and m.feed == planner.cut_feed for m in op.motions)
    assert op.metadata["pass_count"] == 1

    cut_points = [motion.point[:2] for motion in op.motions if motion.move == "linear" and motion.feed == planner.cut_feed]
    assert min(point[0] for point in cut_points) == pytest.approx(-3.0)
    assert max(point[0] for point in cut_points) == pytest.approx(13.0)
    assert min(point[1] for point in cut_points) == pytest.approx(-3.0)
    assert max(point[1] for point in cut_points) == pytest.approx(13.0)


def test_generate_profile_ops_can_leave_stock_before_finishing():
    tool_manager = ToolManager(
        tools=[
            ToolDefinition(
                tool_id="profile_rougher",
                name="Profile Rougher",
                tool_type="end_mill",
                diameter=6.0,
                flute_count=3,
                supported_strategies=("2p5d_profile",),
                supported_modes=("roughing",),
            ),
            ToolDefinition(
                tool_id="profile_finisher",
                name="Profile Finisher",
                tool_type="end_mill",
                diameter=6.0,
                flute_count=4,
                supported_strategies=("2p5d_profile",),
                supported_modes=("finishing",),
            ),
        ],
        preferred_diameter=6.0,
    )
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        tool_manager=tool_manager,
        profile_stock_allowance=0.5,
        max_stepdown=20.0,
    )
    square = [
        (0.0, 0.0, -2.0),
        (10.0, 0.0, -2.0),
        (10.0, 10.0, -2.0),
        (0.0, 10.0, -2.0),
    ]

    plan = planner.generate(features=[], perimeter_polylines=[square])

    assert len(plan.operations) == 2
    roughing_op, finishing_op = plan.operations
    assert roughing_op.metadata["operation_mode"] == "roughing"
    assert roughing_op.metadata["tool_id"] == "profile_rougher"
    assert roughing_op.metadata["radial_stock_to_leave"] == 0.5
    roughing_cut_points = [
        motion.point[:2]
        for motion in roughing_op.motions
        if motion.move == "linear" and motion.feed == planner.cut_feed
    ]
    assert roughing_op.metadata["tool_radius_compensation"] == pytest.approx(3.5)
    assert roughing_op.metadata["cutter_side"] == "outside"
    assert min(point[0] for point in roughing_cut_points) == pytest.approx(-3.5)
    assert max(point[0] for point in roughing_cut_points) == pytest.approx(13.5)
    assert min(point[1] for point in roughing_cut_points) == pytest.approx(-3.5)
    assert max(point[1] for point in roughing_cut_points) == pytest.approx(13.5)
    assert finishing_op.metadata["operation_mode"] == "finishing"
    assert finishing_op.metadata["tool_id"] == "profile_finisher"
    assert "radial_stock_to_leave" not in finishing_op.metadata
    finishing_cut_points = [
        motion.point[:2]
        for motion in finishing_op.motions
        if motion.move == "linear" and motion.feed == planner.cut_feed
    ]
    assert finishing_op.metadata["tool_radius_compensation"] == pytest.approx(3.0)
    assert finishing_op.metadata["cutter_side"] == "outside"
    assert min(point[0] for point in finishing_cut_points) == pytest.approx(-3.0)
    assert max(point[0] for point in finishing_cut_points) == pytest.approx(13.0)
    assert min(point[1] for point in finishing_cut_points) == pytest.approx(-3.0)
    assert max(point[1] for point in finishing_cut_points) == pytest.approx(13.0)


def test_generate_profile_ops_shrink_inner_loops_by_tool_radius():
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        tool_diameter=2.0,
        max_stepdown=20.0,
    )
    outer_square = [
        (0.0, 0.0, -2.0),
        (20.0, 0.0, -2.0),
        (20.0, 20.0, -2.0),
        (0.0, 20.0, -2.0),
    ]
    inner_square = [
        (5.0, 5.0, -2.0),
        (15.0, 5.0, -2.0),
        (15.0, 15.0, -2.0),
        (5.0, 15.0, -2.0),
    ]

    plan = planner.generate(features=[], perimeter_polylines=[outer_square, inner_square])

    assert len(plan.operations) == 2
    outer_op, inner_op = plan.operations
    outer_cut_points = [
        motion.point[:2]
        for motion in outer_op.motions
        if motion.move == "linear" and motion.feed == planner.cut_feed
    ]
    inner_cut_points = [
        motion.point[:2]
        for motion in inner_op.motions
        if motion.move == "linear" and motion.feed == planner.cut_feed
    ]

    assert outer_op.metadata["cutter_side"] == "outside"
    assert inner_op.metadata["cutter_side"] == "inside"
    assert min(point[0] for point in outer_cut_points) == pytest.approx(-1.0)
    assert max(point[0] for point in outer_cut_points) == pytest.approx(21.0)
    assert min(point[1] for point in outer_cut_points) == pytest.approx(-1.0)
    assert max(point[1] for point in outer_cut_points) == pytest.approx(21.0)
    assert min(point[0] for point in inner_cut_points) == pytest.approx(6.0)
    assert max(point[0] for point in inner_cut_points) == pytest.approx(14.0)
    assert min(point[1] for point in inner_cut_points) == pytest.approx(6.0)
    assert max(point[1] for point in inner_cut_points) == pytest.approx(14.0)


def test_profile_stock_offset_uses_occ_geometry_for_concave_loops():
    planner = AutoToolpathPlanner(working_plane_normal=(0.0, 0.0, 1.0), profile_stock_allowance=0.5)
    concave_loop = [
        np.array((0.0, 0.0, -2.0), dtype=float),
        np.array((8.0, 0.0, -2.0), dtype=float),
        np.array((8.0, 3.0, -2.0), dtype=float),
        np.array((5.0, 3.0, -2.0), dtype=float),
        np.array((5.0, 8.0, -2.0), dtype=float),
        np.array((0.0, 8.0, -2.0), dtype=float),
        np.array((0.0, 0.0, -2.0), dtype=float),
    ]

    offset_loops = planner._build_profile_stock_loops(concave_loop, 0.5)

    assert len(offset_loops) == 1
    offset_vertices = {
        (round(float(point[0]), 3), round(float(point[1]), 3))
        for point in offset_loops[0][:-1]
    }
    assert (0.5, 0.5) in offset_vertices
    assert (7.5, 0.5) in offset_vertices
    assert (7.5, 2.5) in offset_vertices
    assert (4.5, 2.5) in offset_vertices
    assert (4.5, 7.5) in offset_vertices
    assert (0.5, 7.5) in offset_vertices
    assert (7.5, 3.5) not in offset_vertices


def test_wires_to_polylines_adapts_sampling_for_occ_circles():
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    circle = gp_Circ(gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0)), 10.0)
    circle_edge = BRepBuilderAPI_MakeEdge(circle).Edge()
    circle_wire = BRepBuilderAPI_MakeWire(circle_edge).Wire()

    polylines = AutoToolpathPlanner.wires_to_polylines([circle_wire], samples_per_edge=3, max_deflection=0.02)

    assert len(polylines) == 1
    circle_points = polylines[0]
    assert len(circle_points) > 6

    radii = [math.hypot(point[0], point[1]) for point in circle_points[:-1]]
    assert max(abs(radius - 10.0) for radius in radii) <= 0.05


def test_generate_slot_milling_ops_from_slot_feature():
    planner = AutoToolpathPlanner(working_plane_normal=(0.0, 0.0, 1.0), safe_z_offset=4.0, max_stepdown=20.0)

    plan = planner.generate(features=[_slot(center=(10.0, 5.0, 0.0), length=20.0, width=6.0, depth=3.0)])

    assert len(plan.operations) == 2
    roughing_op, finishing_op = plan.operations
    assert roughing_op.strategy == "slot_milling"
    assert roughing_op.metadata["operation_mode"] == "roughing"
    assert roughing_op.metadata["length"] == 20.0
    assert roughing_op.metadata["width"] == 6.0
    assert len(roughing_op.motions) == 5
    assert roughing_op.metadata["pass_count"] == 1
    assert roughing_op.motions[2].move == "linear"
    assert roughing_op.motions[2].feed == planner.plunge_feed
    assert roughing_op.motions[2].point[2] == -3.0
    assert roughing_op.motions[3].move == "linear"
    assert roughing_op.motions[3].feed == planner.cut_feed
    assert roughing_op.motions[3].point[0] == 17.0

    assert finishing_op.strategy == "slot_milling"
    assert finishing_op.metadata["operation_mode"] == "finishing"
    assert finishing_op.metadata["finish_path_count"] == 1
    assert finishing_op.metadata["pass_count"] == 1


def test_generate_slot_milling_roughing_uses_multiple_lanes_for_wide_slot():
    tool_manager = ToolManager(
        tools=[
            ToolDefinition(
                tool_id="slot_rougher",
                name="Slot Rougher",
                tool_type="end_mill",
                diameter=4.0,
                flute_count=2,
                supported_strategies=("slot_milling",),
                supported_modes=("roughing",),
            ),
            ToolDefinition(
                tool_id="slot_finisher",
                name="Slot Finisher",
                tool_type="end_mill",
                diameter=4.0,
                flute_count=2,
                supported_strategies=("slot_milling",),
                supported_modes=("finishing",),
            ),
        ],
        preferred_diameter=4.0,
    )
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        safe_z_offset=4.0,
        max_stepdown=20.0,
        stepover_ratio=0.5,
        tool_manager=tool_manager,
    )

    plan = planner.generate(features=[_slot(center=(10.0, 5.0, 0.0), length=20.0, width=12.0, depth=3.0)])

    roughing_op, finishing_op = plan.operations
    assert roughing_op.metadata["operation_mode"] == "roughing"
    assert roughing_op.metadata["roughing_path_count"] == 3
    roughing_cut_points = [
        motion.point
        for motion in roughing_op.motions
        if motion.move == "linear" and motion.feed == planner.cut_feed
    ]
    lane_offsets = sorted({round(point[1] - 5.0, 4) for point in roughing_cut_points})
    assert lane_offsets == [-2.0, 0.0, 2.0]
    assert finishing_op.metadata["finish_path_count"] == 2


def test_generate_slot_milling_uses_trochoidal_roughing_for_tight_engagement_slots():
    tool_manager = ToolManager(
        tools=[
            ToolDefinition(
                tool_id="slot_rougher",
                name="Slot Rougher",
                tool_type="end_mill",
                diameter=4.0,
                flute_count=2,
                supported_strategies=("slot_milling",),
                supported_modes=("roughing",),
            ),
            ToolDefinition(
                tool_id="slot_finisher",
                name="Slot Finisher",
                tool_type="end_mill",
                diameter=4.0,
                flute_count=2,
                supported_strategies=("slot_milling",),
                supported_modes=("finishing",),
            ),
        ],
        preferred_diameter=4.0,
    )
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        safe_z_offset=4.0,
        max_stepdown=20.0,
        stepover_ratio=0.5,
        tool_manager=tool_manager,
    )

    plan = planner.generate(features=[_slot(center=(10.0, 5.0, 0.0), length=20.0, width=6.0, depth=8.0)])

    roughing_op, finishing_op = plan.operations
    assert roughing_op.metadata["operation_mode"] == "roughing"
    assert roughing_op.metadata["roughing_style"] == "trochoidal"
    assert roughing_op.metadata["roughing_path_count"] == 1
    roughing_cut_points = [
        motion.point
        for motion in roughing_op.motions
        if motion.move == "linear" and motion.feed == planner.cut_feed
    ]
    lane_offsets = {round(point[1] - 5.0, 4) for point in roughing_cut_points}
    assert len(lane_offsets) > 3
    assert max(abs(offset) for offset in lane_offsets) > 0.5
    assert finishing_op.metadata["finish_path_count"] == 2


def test_generate_cavity_clearing_ops_from_pocket_feature():
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        safe_z_offset=4.0,
        max_stepdown=2.0,
        tool_diameter=4.0,
        stepover_ratio=0.5,
    )

    plan = planner.generate(features=[_cavity("pocket", center=(0.0, 0.0, -6.0), span=(20.0, 10.0), depth=6.0)])

    assert len(plan.operations) == 2
    assert plan.safe_projection == 4.0
    roughing_op, finishing_op = plan.operations
    assert roughing_op.strategy == "cavity_clearing"
    assert roughing_op.feature_type == "pocket"
    assert roughing_op.metadata["operation_mode"] == "roughing"
    assert roughing_op.metadata["pass_count"] == 3
    assert roughing_op.metadata["line_count"] == 4
    plunge_moves = [motion for motion in roughing_op.motions if motion.move == "linear" and motion.feed == planner.plunge_feed]
    assert len(plunge_moves) == 12
    assert plunge_moves[0].point[2] == -2.0
    assert plunge_moves[-1].point[2] == -6.0
    assert finishing_op.strategy == "cavity_clearing"
    assert finishing_op.metadata["operation_mode"] == "finishing"
    assert finishing_op.metadata["boundary_point_count"] == 5
    assert finishing_op.metadata["pass_count"] == 3


def test_generate_cavity_clearing_uses_boundary_loops_for_real_geometry():
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        safe_z_offset=4.0,
        max_stepdown=4.0,
        tool_diameter=2.0,
        stepover_ratio=0.5,
    )
    feature = _cavity_with_boundary(
        "pocket",
        boundary_loops=[
            [
                (0.0, 0.0, -4.0),
                (10.0, 0.0, -4.0),
                (0.0, 10.0, -4.0),
            ]
        ],
        depth=4.0,
    )

    plan = planner.generate(features=[feature])

    roughing_op, finishing_op = plan.operations
    assert roughing_op.metadata["clearing_style"] == "contour_parallel"
    assert finishing_op.metadata["boundary_style"] == "offset_boundary"

    roughing_cut_points = [
        motion.point
        for motion in roughing_op.motions
        if motion.move == "linear" and motion.feed == planner.cut_feed
    ]
    finishing_cut_points = [
        motion.point
        for motion in finishing_op.motions
        if motion.move == "linear" and motion.feed == planner.cut_feed
    ]

    assert roughing_cut_points
    assert finishing_cut_points
    assert all(point[0] >= -1e-6 and point[1] >= -1e-6 and point[0] + point[1] <= 10.0001 for point in roughing_cut_points)
    assert all(point[0] >= -1e-6 and point[1] >= -1e-6 and point[0] + point[1] <= 10.0001 for point in finishing_cut_points)


def test_real_pocket_loops_do_not_fall_back_to_bbox_on_bottle_opener():
    step_path = "tests/data/bottle_opener.step"
    if not os.path.exists(step_path):
        pytest.skip(f"Test fixture not found: {step_path}")

    model = Model.from_step(step_path)
    extractor = FeatureExtractor(model)
    features = extractor.extract()
    working_plane_normal = tuple(float(value) for value in extractor.working_plane_normal)
    contour_extractor = ContourExtractor(model, working_plane_normal=working_plane_normal, features=features)
    contour_shadow = contour_extractor.extract()
    perimeter = contour_extractor.extract_perimeter(contour_shadow) if contour_shadow is not None else []
    planner = AutoToolpathPlanner(
        working_plane_normal=working_plane_normal,
        safe_z_offset=4.0,
        max_stepdown=4.0,
        tool_diameter=6.0,
        stepover_ratio=0.5,
        profile_stock_allowance=0.4,
    )

    plan = planner.generate(features=features, perimeter_wires=perimeter)

    loop_based_roughing = [
        operation
        for operation in plan.operations
        if operation.strategy == "cavity_clearing"
        and operation.metadata.get("operation_mode") == "roughing"
        and operation.metadata.get("geometry_source") == "feature_boundary_loops"
    ]
    loop_based_finishing = [
        operation
        for operation in plan.operations
        if operation.strategy == "cavity_clearing"
        and operation.metadata.get("operation_mode") == "finishing"
        and operation.metadata.get("geometry_source") == "feature_boundary_loops"
    ]

    assert loop_based_roughing
    assert loop_based_finishing
    assert all(operation.metadata.get("clearing_style") != "raster_bbox" for operation in loop_based_roughing)
    assert all(operation.metadata.get("boundary_style") != "bbox_rectangle" for operation in loop_based_finishing)


def test_real_flange_step_faces_do_not_generate_automatic_cavity_clearing():
    step_path = "tests/data/flange.step"
    if not os.path.exists(step_path):
        pytest.skip(f"Test fixture not found: {step_path}")

    model = Model.from_step(step_path)
    extractor = FeatureExtractor(model)
    features = extractor.extract()
    working_plane_normal = tuple(float(value) for value in extractor.working_plane_normal)
    contour_extractor = ContourExtractor(model, working_plane_normal=working_plane_normal, features=features)
    contour_shadow = contour_extractor.extract()
    perimeter = contour_extractor.extract_perimeter(contour_shadow) if contour_shadow is not None else []
    planner = AutoToolpathPlanner(
        working_plane_normal=working_plane_normal,
        safe_z_offset=4.0,
        max_stepdown=4.0,
        tool_diameter=6.0,
        stepover_ratio=0.5,
    )

    plan = planner.generate(features=features, perimeter_wires=perimeter)

    step_ops = [
        operation
        for operation in plan.operations
        if operation.strategy == "cavity_clearing" and operation.feature_type == "step"
    ]

    assert step_ops == []
    assert any(
        "requires stock/setup-aware planning; skipping automatic cavity clearing" in warning
        for warning in plan.warnings
    )


def test_real_flange_piece_only_shadow_keeps_internal_hole_perimeters():
    step_path = "tests/data/flange.step"
    if not os.path.exists(step_path):
        pytest.skip(f"Test fixture not found: {step_path}")

    model = Model.from_step(step_path)
    contour_extractor = ContourExtractor(model, working_plane_normal=(0.0, 0.0, 1.0), features=[])
    contour_shadow = contour_extractor.extract()
    perimeter = contour_extractor.extract_perimeter(contour_shadow) if contour_shadow is not None else []

    assert len(perimeter) == 6


def test_real_flange_perimeter_wire_sampling_preserves_connected_loop_order():
    step_path = "tests/data/flange.step"
    if not os.path.exists(step_path):
        pytest.skip(f"Test fixture not found: {step_path}")

    model = Model.from_step(step_path)
    contour_extractor = ContourExtractor(model, working_plane_normal=(0.0, 0.0, 1.0), features=[])
    contour_shadow = contour_extractor.extract()
    perimeter = contour_extractor.extract_perimeter(contour_shadow) if contour_shadow is not None else []
    planner = AutoToolpathPlanner(working_plane_normal=(0.0, 0.0, 1.0))

    contour_loops = planner.wires_to_polylines(perimeter, samples_per_edge=8, max_deflection=0.05)
    outer_loop = planner._normalize_planar_loops(contour_loops)[0]
    segment_lengths = [
        float(np.linalg.norm(outer_loop[index + 1] - outer_loop[index]))
        for index in range(len(outer_loop) - 1)
    ]

    assert segment_lengths
    assert max(segment_lengths) < 20.0


def test_drilling_uses_multiple_depth_passes():
    planner = AutoToolpathPlanner(working_plane_normal=(0.0, 0.0, 1.0), max_stepdown=3.0)

    plan = planner.generate(features=[_hole_group([(10.0, 5.0, 0.0)], depth=8.0, through=False)])

    op = plan.operations[0]
    plunge_moves = [motion for motion in op.motions if motion.move == "linear"]
    assert op.metadata["pass_count"] == 3
    assert [motion.point[2] for motion in plunge_moves] == [-3.0, -6.0, -8.0]


def test_profile_uses_multiple_depth_passes():
    planner = AutoToolpathPlanner(working_plane_normal=(0.0, 0.0, 1.0), max_stepdown=2.5)
    square = [
        (0.0, 0.0, -6.0),
        (10.0, 0.0, -6.0),
        (10.0, 10.0, -6.0),
        (0.0, 10.0, -6.0),
    ]

    plan = planner.generate(features=[], perimeter_polylines=[square])

    op = plan.operations[0]
    plunge_moves = [motion for motion in op.motions if motion.move == "linear" and motion.feed == planner.plunge_feed]
    assert op.metadata["pass_count"] == 3
    assert [motion.point[2] for motion in plunge_moves] == [-2.5, -5.0, -6.0]


def test_manual_parameters_can_override_tool_and_feeds_per_strategy():
    tool_manager = ToolManager(
        tools=[
            ToolDefinition(tool_id="slot_finisher", name="Slot Finisher", tool_type="end_mill", diameter=3.0, flute_count=2),
            ToolDefinition(tool_id="general_drill", name="General Drill", tool_type="drill", diameter=6.0, flute_count=2),
        ],
        strategy_tool_map={"slot_milling": "slot_finisher"},
        preferred_diameter=6.0,
    )
    parameter_resolver = MachiningParameterResolver(
        mode="manual",
        base_overrides=ParameterOverrides(
            cut_feed=180.0,
            plunge_feed=60.0,
            max_stepdown=10.0,
            lead_in_distance=2.0,
            lead_out_distance=1.0,
        ),
        strategy_overrides={
            "slot_milling": ParameterOverrides(cut_feed=250.0, plunge_feed=75.0),
        },
    )
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        safe_z_offset=4.0,
        tool_manager=tool_manager,
        parameter_resolver=parameter_resolver,
    )

    plan = planner.generate(features=[_slot(center=(10.0, 5.0, 0.0), length=20.0, width=6.0, depth=3.0)])

    roughing_op = plan.operations[0]
    assert roughing_op.metadata["tool_id"] == "slot_finisher"
    assert roughing_op.metadata["parameter_mode"] == "manual"
    assert roughing_op.metadata["operation_mode"] == "roughing"
    assert roughing_op.metadata["cut_feed"] == 250.0
    assert roughing_op.metadata["plunge_feed"] == 75.0
    assert roughing_op.metadata["lead_in_distance"] == 2.0
    assert roughing_op.metadata["lead_out_distance"] == 1.0
    assert roughing_op.motions[2].point[0] == 1.0
    assert roughing_op.motions[3].point[0] == 3.0
    assert roughing_op.motions[-2].point[0] == 18.0


def test_automatic_parameters_select_tool_and_calculate_feeds():
    tool_manager = ToolManager(
        tools=[
            ToolDefinition(tool_id="small_em", name="Small End Mill", tool_type="end_mill", diameter=3.0, flute_count=2),
            ToolDefinition(tool_id="large_em", name="Large End Mill", tool_type="end_mill", diameter=8.0, flute_count=4),
        ],
        preferred_diameter=8.0,
    )
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        parameter_mode="automatic",
        tool_manager=tool_manager,
    )

    plan = planner.generate(features=[_slot(center=(10.0, 5.0, 0.0), width=4.0, length=18.0, depth=4.0)])

    op = plan.operations[0]
    expected_spindle = (120.0 * 1000.0) / (math.pi * 3.0)
    expected_cut_feed = expected_spindle * 2 * 0.02
    expected_plunge_feed = expected_cut_feed * 0.35
    assert op.metadata["tool_id"] == "small_em"
    assert op.metadata["parameter_mode"] == "automatic"
    assert op.metadata["operation_mode"] == "roughing"
    assert op.metadata["spindle_speed"] == pytest.approx(expected_spindle, rel=1e-4)
    assert op.metadata["cut_feed"] == pytest.approx(expected_cut_feed, rel=1e-4)
    assert op.metadata["plunge_feed"] == pytest.approx(expected_plunge_feed, rel=1e-4)
    assert op.metadata["max_stepdown"] == 1.5
    assert op.metadata["lead_in_distance"] == 0.75
    assert op.metadata["lead_out_distance"] == 0.75


def test_operations_are_ordered_for_safe_sequence():
    planner = AutoToolpathPlanner(working_plane_normal=(0.0, 0.0, 1.0), max_stepdown=20.0)
    square = [
        (0.0, 0.0, -2.0),
        (10.0, 0.0, -2.0),
        (10.0, 10.0, -2.0),
        (0.0, 10.0, -2.0),
    ]

    plan = planner.generate(
        features=[
            _slot(center=(10.0, 5.0, 0.0), length=20.0, width=6.0, depth=3.0),
            _cavity("pocket", center=(0.0, 0.0, -6.0), span=(20.0, 10.0), depth=6.0),
            _hole_group([(5.0, 5.0, 0.0)], diameter=6.0, depth=4.0),
        ],
        perimeter_polylines=[square],
    )

    assert [(op.strategy, op.metadata.get("operation_mode")) for op in plan.operations] == [
        ("drilling", "drilling"),
        ("cavity_clearing", "roughing"),
        ("cavity_clearing", "finishing"),
        ("slot_milling", "roughing"),
        ("slot_milling", "finishing"),
    ]
    assert [op.metadata["sequence"] for op in plan.operations] == [0, 1, 2, 3, 4]
    assert any("Perimeter profile fallback skipped" in warning for warning in plan.warnings)


def test_tool_manager_respects_strategy_and_depth_limits():
    tool_manager = ToolManager(
        tools=[
            ToolDefinition(
                tool_id="slot_shallow",
                name="Slot Shallow",
                tool_type="end_mill",
                diameter=4.0,
                flute_count=3,
                flute_length=4.0,
                stickout=8.0,
                tool_material="carbide",
                compatible_materials=("aluminum",),
                supported_strategies=("slot_milling",),
                supported_modes=("roughing",),
            ),
            ToolDefinition(
                tool_id="slot_deep",
                name="Slot Deep",
                tool_type="end_mill",
                diameter=3.0,
                flute_count=3,
                flute_length=12.0,
                stickout=20.0,
                tool_material="carbide",
                compatible_materials=("aluminum", "steel"),
                supported_strategies=("slot_milling",),
                supported_modes=("roughing",),
            ),
            ToolDefinition(
                tool_id="profile_only",
                name="Profile Only",
                tool_type="end_mill",
                diameter=4.0,
                flute_count=3,
                flute_length=10.0,
                stickout=18.0,
                tool_material="carbide",
                compatible_materials=("aluminum",),
                supported_strategies=("2p5d_profile",),
                supported_modes=("finishing",),
            ),
            ToolDefinition(
                tool_id="profile_steel",
                name="Profile Steel",
                tool_type="end_mill",
                diameter=4.0,
                flute_count=3,
                flute_length=10.0,
                stickout=18.0,
                tool_material="carbide",
                compatible_materials=("steel",),
                supported_strategies=("2p5d_profile",),
                supported_modes=("finishing",),
            ),
        ],
        preferred_diameter=4.0,
        workpiece_material="aluminum",
    )

    slot_request = OperationRequest(
        strategy="slot_milling",
        feature_type="slot",
        operation_mode="roughing",
        depth=10.0,
        width=5.0,
        length=20.0,
    )
    profile_request = OperationRequest(
        strategy="2p5d_profile",
        feature_type="perimeter",
        operation_mode="finishing",
        depth=2.0,
        span_u=12.0,
        span_v=10.0,
    )

    selected_slot_tool = tool_manager.select_tool(slot_request)
    selected_profile_tool = tool_manager.select_tool(profile_request)

    assert selected_slot_tool is not None
    assert selected_slot_tool.tool_id == "slot_deep"
    assert selected_profile_tool is not None
    assert selected_profile_tool.tool_id == "profile_only"


def test_generated_operations_include_operation_modes_in_metadata():
    planner = AutoToolpathPlanner(working_plane_normal=(0.0, 0.0, 1.0), max_stepdown=20.0)
    square = [
        (0.0, 0.0, -2.0),
        (10.0, 0.0, -2.0),
        (10.0, 10.0, -2.0),
        (0.0, 10.0, -2.0),
    ]

    plan = planner.generate(
        features=[
            _hole_group([(5.0, 5.0, 0.0)], diameter=6.0, depth=4.0),
            _slot(center=(10.0, 5.0, 0.0), length=20.0, width=6.0, depth=3.0),
            _cavity("pocket", center=(0.0, 0.0, -6.0), span=(20.0, 10.0), depth=6.0),
        ],
        perimeter_polylines=[square],
    )

    mode_pairs = [(op.strategy, op.metadata.get("operation_mode")) for op in plan.operations]

    assert ("drilling", "drilling") in mode_pairs
    assert ("slot_milling", "roughing") in mode_pairs
    assert ("slot_milling", "finishing") in mode_pairs
    assert ("cavity_clearing", "roughing") in mode_pairs
    assert ("cavity_clearing", "finishing") in mode_pairs


def test_perimeter_profile_fallback_is_skipped_when_semantic_ops_exist_by_default():
    planner = AutoToolpathPlanner(working_plane_normal=(0.0, 0.0, 1.0), max_stepdown=20.0)
    square = [
        (0.0, 0.0, -2.0),
        (10.0, 0.0, -2.0),
        (10.0, 10.0, -2.0),
        (0.0, 10.0, -2.0),
    ]

    plan = planner.generate(
        features=[_hole_group([(5.0, 5.0, 0.0)], diameter=6.0, depth=4.0)],
        perimeter_polylines=[square],
    )

    assert all(operation.strategy != "2p5d_profile" for operation in plan.operations)
    assert any("Perimeter profile fallback skipped" in warning for warning in plan.warnings)


def test_generated_operations_include_feature_based_machining_metadata():
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        max_stepdown=20.0,
        tool_diameter=4.0,
        stepover_ratio=0.5,
        enable_perimeter_profile_fallback=True,
    )
    square = [
        (0.0, 0.0, -2.0),
        (10.0, 0.0, -2.0),
        (10.0, 10.0, -2.0),
        (0.0, 10.0, -2.0),
    ]
    pocket = _cavity_with_boundary(
        "pocket",
        boundary_loops=[[square[0], square[1], square[2], square[3]]],
        depth=4.0,
    )

    plan = planner.generate(
        features=[
            _hole_group([(5.0, 5.0, 0.0)], diameter=6.0, depth=4.0),
            _slot(center=(10.0, 5.0, 0.0), length=20.0, width=6.0, depth=8.0),
            pocket,
        ],
        perimeter_polylines=[square],
    )

    drilling_op = next(op for op in plan.operations if op.strategy == "drilling")
    slot_roughing_op = next(
        op for op in plan.operations if op.strategy == "slot_milling" and op.metadata["operation_mode"] == "roughing"
    )
    cavity_roughing_op = next(
        op for op in plan.operations if op.strategy == "cavity_clearing" and op.metadata["operation_mode"] == "roughing"
    )
    profile_op = next(op for op in plan.operations if op.strategy == "2p5d_profile")

    assert drilling_op.metadata["machining_class"] == "hole_making"
    assert drilling_op.metadata["feature_subtype"] == "hole_group"
    assert drilling_op.metadata["geometry_source"] == "hole_centers"

    assert slot_roughing_op.metadata["machining_class"] == "slot_milling"
    assert slot_roughing_op.metadata["feature_subtype"] == "closed_slot"
    assert slot_roughing_op.metadata["geometry_source"] == "slot_axis"
    assert slot_roughing_op.metadata["roughing_style"] == "trochoidal"

    assert cavity_roughing_op.metadata["machining_class"] == "pocket_milling"
    assert cavity_roughing_op.metadata["feature_subtype"] == "closed_pocket"
    assert cavity_roughing_op.metadata["geometry_source"] == "feature_boundary_loops"

    assert profile_op.metadata["machining_class"] == "profile_milling"
    assert profile_op.metadata["feature_subtype"] == "closed_profile"
    assert profile_op.metadata["geometry_source"] == "perimeter_polyline_fallback"


def test_perimeter_profile_fallback_can_be_enabled_explicitly():
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        max_stepdown=20.0,
        enable_perimeter_profile_fallback=True,
    )
    square = [
        (0.0, 0.0, -2.0),
        (10.0, 0.0, -2.0),
        (10.0, 10.0, -2.0),
        (0.0, 10.0, -2.0),
    ]

    plan = planner.generate(
        features=[_hole_group([(5.0, 5.0, 0.0)], diameter=6.0, depth=4.0)],
        perimeter_polylines=[square],
    )

    assert any(operation.strategy == "2p5d_profile" for operation in plan.operations)


def test_profile_rough_only_mode_emits_only_profile_roughing_ops():
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        max_stepdown=20.0,
        profile_stock_allowance=0.4,
        profile_rough_only=True,
    )
    square = [
        (0.0, 0.0, -2.0),
        (10.0, 0.0, -2.0),
        (10.0, 10.0, -2.0),
        (0.0, 10.0, -2.0),
    ]

    plan = planner.generate(
        features=[
            _hole_group([(5.0, 5.0, 0.0)], diameter=6.0, depth=4.0),
            _slot(center=(10.0, 5.0, 0.0), length=20.0, width=6.0, depth=3.0),
            _cavity("pocket", center=(0.0, 0.0, -6.0), span=(20.0, 10.0), depth=6.0),
        ],
        perimeter_polylines=[square],
    )

    assert [(op.strategy, op.metadata.get("operation_mode")) for op in plan.operations] == [
        ("2p5d_profile", "roughing"),
    ]
    assert plan.operations[0].metadata["geometry_source"] == "perimeter_polyline_fallback"
    assert any("Profile rough only mode enabled" in warning for warning in plan.warnings)


def test_profile_rough_only_mode_uses_only_outer_perimeter_loop():
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        max_stepdown=20.0,
        profile_stock_allowance=0.4,
        profile_rough_only=True,
    )
    outer_square = [
        (-10.0, -10.0, -2.0),
        (10.0, -10.0, -2.0),
        (10.0, 10.0, -2.0),
        (-10.0, 10.0, -2.0),
    ]
    inner_square = [
        (-4.0, -4.0, -2.0),
        (4.0, -4.0, -2.0),
        (4.0, 4.0, -2.0),
        (-4.0, 4.0, -2.0),
    ]

    plan = planner.generate(
        features=[_hole_group([(0.0, 0.0, 0.0)], diameter=6.0, depth=4.0)],
        perimeter_polylines=[outer_square, inner_square],
    )

    assert [(op.strategy, op.metadata.get("operation_mode")) for op in plan.operations] == [
        ("2p5d_profile", "roughing"),
    ]
    assert plan.operations[0].metadata["span_u"] > 15.0
    assert plan.operations[0].metadata["span_v"] > 15.0


def test_piece_roughing_only_mode_ignores_semantic_features_and_uses_perimeter_baseline():
    planner = AutoToolpathPlanner(
        working_plane_normal=(0.0, 0.0, 1.0),
        max_stepdown=1.5,
        piece_roughing_only=True,
    )
    square = [
        (-10.0, -10.0, -2.0),
        (10.0, -10.0, -2.0),
        (10.0, 10.0, -2.0),
        (-10.0, 10.0, -2.0),
    ]

    plan = planner.generate(
        features=[
            _hole_group([(0.0, 0.0, 0.0)], diameter=6.0, depth=4.0),
            _slot(center=(10.0, 5.0, 0.0), length=20.0, width=6.0, depth=3.0),
            _cavity("pocket", center=(0.0, 0.0, -6.0), span=(20.0, 10.0), depth=6.0),
        ],
        perimeter_polylines=[square],
        piece_top_projection=0.0,
        piece_bottom_projection=-4.0,
    )

    assert [(op.strategy, op.feature_type, op.metadata.get("operation_mode")) for op in plan.operations] == [
        ("cavity_clearing", "piece", "roughing"),
    ]
    assert plan.operations[0].metadata["piece_based"] is True
    assert plan.operations[0].metadata["depth"] == pytest.approx(4.0)
    assert plan.operations[0].metadata["pass_count"] == 3
    assert plan.operations[0].metadata["feature_subtype"] == "piece_shadow"
    assert plan.operations[0].metadata["geometry_source"] == "perimeter_polyline_fallback"
    cut_levels = {
        round(motion.point[2], 4)
        for motion in plan.operations[0].motions
        if motion.move == "linear" and motion.feed == plan.operations[0].metadata["cut_feed"]
    }
    assert cut_levels == {-1.5, -3.0, -4.0}
    assert not any("skipping slot milling" in warning for warning in plan.warnings)
    assert any("Piece rough only mode enabled" in warning for warning in plan.warnings)


def test_slot_milling_warns_when_slot_axis_has_no_in_plane_direction():
    planner = AutoToolpathPlanner(working_plane_normal=(0.0, 0.0, 1.0))

    plan = planner.generate(features=[_slot(center=(0.0, 0.0, 0.0), axis=(0.0, 0.0, 1.0))])

    assert plan.operations == []
    assert len(plan.warnings) == 1
    assert "skipping slot milling" in plan.warnings[0]


def test_plan_warning_when_no_input_data():
    planner = AutoToolpathPlanner()

    plan = planner.generate(features=[])

    assert plan.operations == []
    assert len(plan.warnings) == 1
    assert "No operations generated" in plan.warnings[0]
