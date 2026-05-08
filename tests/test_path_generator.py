from types import SimpleNamespace

from antcam.path_generator import AutoToolpathPlanner


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


def test_generate_drilling_ops_from_hole_group():
    planner = AutoToolpathPlanner(working_plane_normal=(0.0, 0.0, 1.0), safe_z_offset=4.0)
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
    assert op.motions[0].move == "rapid"
    assert any(m.move == "linear" and m.feed == planner.cut_feed for m in op.motions)


def test_plan_warning_when_no_input_data():
    planner = AutoToolpathPlanner()

    plan = planner.generate(features=[])

    assert plan.operations == []
    assert len(plan.warnings) == 1
    assert "No operations generated" in plan.warnings[0]
