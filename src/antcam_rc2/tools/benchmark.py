"""Performance regression harness for AntCAM RC2.

Runs canonical scenarios (corpus import -> plan / simulate / post / render),
prints a table of medians (3 runs each) and **fails with exit code 1** when a
median exceeds the budget recorded in ``tools/benchmarks.json`` (budget =
baseline x 2 for anti-flakiness).

Setup is performed once per scenario and is excluded from the timing: the
measured region is the operation itself (plan/simulate/post/render), exactly
as the Phase 8 baselines were taken.

Usage:
    python tools/benchmark.py            # run all scenarios
    python tools/benchmark.py --scenario plan_stress --runs 5

The budgets are provisional (development-hardware baselines x 2) and must be
recalibrated on the first CI run (see PLAN_FASE_8 section 8.10, step 8).
"""

from __future__ import annotations

import argparse
import atexit
import json
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
_BUDGET_FILE = _TOOLS / "benchmarks.json"

_APPS: list = []


def _register(app) -> None:
    """Keep the Application alive for the process and shut it down at exit."""
    _APPS.append(app)


def _shutdown_all() -> None:
    for app in _APPS:
        app.shutdown()


atexit.register(_shutdown_all)


def _timeit(measure: Callable[[], None], runs: int = 3) -> float:
    """Return the median wall-clock of ``runs`` executions of ``measure``."""
    timings = []
    for _ in range(runs):
        start = time.perf_counter()
        measure()
        timings.append(time.perf_counter() - start)
    return statistics.median(timings)


def _make_plan_scene(ops: int) -> tuple:
    """Build a small project + scene + toolpath service for plan scenarios."""
    from antcam_rc2.core.feeds_speeds.calculator import FeedsSpeedsCalculator
    from antcam_rc2.core.geometry.curves import Circle, LineSegment
    from antcam_rc2.core.geometry.paths import Contour
    from antcam_rc2.core.geometry.primitives import Point2
    from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
    from antcam_rc2.core.operations.registry import build_standard_registry
    from antcam_rc2.core.project.geometry_refs import create_geometry_ref
    from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
    from antcam_rc2.core.toolpath.service import PlanningSettings, ToolpathService

    scene = GeometryScene(source=SourceInfo(format="dxf", path="bench.dxf"))
    points = [Point2(0.0, 0.0), Point2(40.0, 0.0), Point2(40.0, 20.0), Point2(0.0, 20.0)]
    scene.add_entity(
        Contour([LineSegment(a, b) for a, b in zip(points, points[1:] + [points[0]], strict=True)]),
        "profile",
    )
    scene.add_entity(Circle(Point2(10.0, 10.0), 3.0), "holes")

    app = _new_application()
    project = app.project_service.create_project(
        "bench",
        machine_id="makera_z1",
        stock=Stock(width_mm=60.0, length_mm=40.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    app.project_service.attach_geometry(project.id, scene)
    for index in range(ops):
        kind = OperationType.PROFILING if index % 2 == 0 else OperationType.DRILL
        layer = "profile" if kind is OperationType.PROFILING else "holes"
        app.project_service.add_operation(
            project.id,
            kind,
            tool_id="end_mill_3_175_2f" if kind is OperationType.PROFILING else "drill_2",
            cooling_id="aerodust",
            geometry_refs=(create_geometry_ref(scene, layer, 0),),
            operation_parameters=OperationParameters(depth_mm=2.0, stepdown_mm=1.0),
        )
    service = ToolpathService(
        project_service=app.project_service,
        catalog_repository=app.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )
    snapshot = app.project_service.get_project(project.id)
    settings = PlanningSettings(clearance_z_mm=5.0)
    return app, scene, service, snapshot, settings


def _stress_scene() -> tuple:
    """Build the 200-ref x 200-segment stress scenario (Phase 8.2 baseline)."""
    from antcam_rc2.core.feeds_speeds.calculator import FeedsSpeedsCalculator
    from antcam_rc2.core.geometry.curves import LineSegment
    from antcam_rc2.core.geometry.paths import Contour
    from antcam_rc2.core.geometry.primitives import Point2
    from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
    from antcam_rc2.core.operations.registry import build_standard_registry
    from antcam_rc2.core.project.geometry_refs import create_geometry_ref
    from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
    from antcam_rc2.core.toolpath.service import PlanningSettings, ToolpathService

    scene = GeometryScene(source=SourceInfo(format="dxf", path="stress.dxf"))
    for contour in range(200):
        points = [Point2(contour * 0.5 + s * 0.1, (s % 50) * 0.1) for s in range(200)]
        scene.add_entity(
            Contour([LineSegment(a, b) for a, b in zip(points, points[1:], strict=False)]),
            f"layer_{contour % 20}",
        )
    app = _new_application()
    project = app.project_service.create_project(
        "stress",
        machine_id="makera_z1",
        stock=Stock(width_mm=100.0, length_mm=100.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    app.project_service.attach_geometry(project.id, scene)
    for index in range(200):
        app.project_service.add_operation(
            project.id,
            OperationType.PROFILING,
            tool_id="end_mill_3_175_2f",
            cooling_id="aerodust",
            geometry_refs=(create_geometry_ref(scene, f"layer_{index % 20}", index // 20),),
            operation_parameters=OperationParameters(depth_mm=2.0),
        )
    service = ToolpathService(
        project_service=app.project_service,
        catalog_repository=app.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )
    snapshot = app.project_service.get_project(project.id)
    settings = PlanningSettings(clearance_z_mm=5.0)
    return app, scene, service, snapshot, settings


def _new_application():
    from antcam_rc2.app.application import Application

    app = Application()
    _register(app)
    return app


def scenario_plan_50_ops() -> Callable[[], None]:
    app, scene, service, snapshot, settings = _make_plan_scene(50)
    return lambda: service.plan_snapshot(snapshot, scene, settings)


def scenario_plan_stress_first() -> Callable[[], None]:
    from antcam_rc2.core.project.geometry_refs import invalidate_scene_index_cache

    app, scene, service, snapshot, settings = _stress_scene()

    def measure() -> None:
        invalidate_scene_index_cache()  # every run measures the cold index build
        service.plan_snapshot(snapshot, scene, settings)

    return measure


def scenario_plan_stress_replan() -> Callable[[], None]:
    app, scene, service, snapshot, settings = _stress_scene()
    service.plan_snapshot(snapshot, scene, settings)  # warm the scene index cache
    return lambda: service.plan_snapshot(snapshot, scene, settings)


def scenario_simulate_20_ops() -> Callable[[], None]:
    from antcam_rc2.core.simulation.models import SimulationSettings
    from antcam_rc2.core.simulation.simulator import Simulator

    app, scene, service, snapshot, settings = _make_plan_scene(20)
    plan = service.plan_snapshot(snapshot, scene, settings)
    assert plan.is_executable
    return lambda: Simulator(app.catalog_repository).simulate(
        snapshot, plan, SimulationSettings(voxel_resolution_mm=1.0)
    )


def scenario_post_12k_motion() -> Callable[[], None]:
    from antcam_rc2.core.post import PostService

    app, scene, service, snapshot, settings = _make_plan_scene(80)
    plan = service.plan_snapshot(snapshot, scene, settings)
    assert plan.is_executable
    return lambda: PostService(app.catalog_repository).post(snapshot, plan)


def scenario_render_5k_entities() -> Callable[[], None]:
    from antcam_rc2.core.geometry.curves import Circle, LineSegment
    from antcam_rc2.core.geometry.primitives import Point2
    from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
    from antcam_rc2.core.rendering.builder import geometry_to_scene

    scene = GeometryScene(source=SourceInfo(format="dxf", path="render.dxf"))
    for index in range(5000):
        if index % 2 == 0:
            scene.add_entity(
                LineSegment(Point2(index * 0.01, 0.0), Point2(index * 0.01 + 1.0, 1.0)),
                "lines",
            )
        else:
            scene.add_entity(Circle(Point2(index * 0.01, 1.0), 0.5), "circles")
    return lambda: geometry_to_scene(scene)


def scenario_import3d_stl() -> Callable[[], None]:
    from antcam_rc2.core.io3d import import_file_3d

    path = _TOOLS.parent / "tests" / "data" / "MALE_BUCKLE.stl"
    import_file_3d(str(path))  # warm trimesh import + file cache (setup)
    return lambda: import_file_3d(str(path))


def scenario_import3d_step() -> Callable[[], None]:
    from antcam_rc2.core.io3d import import_file_3d

    path = _TOOLS.parent / "tests" / "data" / "bottle_opener.step"
    import_file_3d(str(path))  # warm OCP import + file cache (setup)
    return lambda: import_file_3d(str(path))


def scenario_picking_3d() -> Callable[[], None]:
    from antcam_rc2.core.geometry3d.mesh import TriMesh
    from antcam_rc2.core.geometry3d.picking import ray_mesh_hit
    from antcam_rc2.core.geometry3d.scene import SolidBody, SolidScene, SolidSourceInfo

    vertices, faces = _subdivided_plane(64)
    mesh = TriMesh(vertices=vertices, faces=faces)
    body = SolidBody(id="b", name="box", mesh=mesh)
    scene = SolidScene(source=SolidSourceInfo(format="stl"), bodies=(body,), tolerance_mm=0.01)
    ray_mesh_hit(scene, (0.5, 0.5, 2.0), (0.0, 0.0, -1.0))  # warm numpy einsum (setup)
    return lambda: ray_mesh_hit(scene, (0.5, 0.5, 2.0), (0.0, 0.0, -1.0))


def scenario_render_3d_mesh() -> Callable[[], None]:
    from antcam_rc2.core.geometry3d.mesh import TriMesh
    from antcam_rc2.core.geometry3d.scene import SolidBody, SolidScene, SolidSourceInfo
    from antcam_rc2.core.rendering.builder import solid_to_scene

    vertices, faces = _subdivided_plane(64)
    mesh = TriMesh(vertices=vertices, faces=faces)
    body = SolidBody(id="b", name="box", mesh=mesh)
    scene = SolidScene(source=SolidSourceInfo(format="stl"), bodies=(body,), tolerance_mm=0.01)
    solid_to_scene(scene)  # warm first build (setup)
    return lambda: solid_to_scene(scene)


def scenario_plan3d_feature() -> Callable[[], None]:
    from antcam_rc2.core.feeds_speeds.calculator import FeedsSpeedsCalculator
    from antcam_rc2.core.geometry3d.scene import FeatureKind
    from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
    from antcam_rc2.core.io3d import import_file_3d
    from antcam_rc2.core.operations.registry import build_standard_registry
    from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
    from antcam_rc2.core.project.solid_refs import create_solid_ref
    from antcam_rc2.core.toolpath.service import PlanningSettings, ToolpathService

    app = _new_application()
    project = app.project_service.create_project(
        "bench3d",
        machine_id="makera_z1",
        stock=Stock(width_mm=60.0, length_mm=60.0, height_mm=20.0, material_id="aluminum_6061"),
    )
    solid = import_file_3d(str(_TOOLS.parent / "tests" / "data" / "MALE_BUCKLE.stl"))
    body_index, feature_index, _feature = next(
        (bi, fi, feature)
        for bi, body in enumerate(solid.bodies)
        for fi, feature in enumerate(body.features)
        if feature.kind is FeatureKind.FACE_PLANAR and feature.facing
    )
    reference = create_solid_ref(solid, body_index, feature_index)
    app.project_service.add_operation(
        project.id,
        OperationType.POCKETING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        solid_refs=(reference,),
        operation_parameters=OperationParameters(depth_mm=1.0),
    )
    snapshot = app.project_service.get_project(project.id)
    empty = GeometryScene(source=SourceInfo(format="dxf"))
    service = ToolpathService(
        project_service=app.project_service,
        catalog_repository=app.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )
    settings = PlanningSettings(clearance_z_mm=5.0)
    service.plan_snapshot(snapshot, empty, settings, solid_scene=solid)  # warm (setup)
    return lambda: service.plan_snapshot(snapshot, empty, settings, solid_scene=solid)


def _subdivided_plane(segments: int):
    """A subdivided unit quad on z=0 (for picking/render benchmarks)."""
    import numpy as np

    lin = np.linspace(0.0, 1.0, segments + 1)
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    grid: dict[tuple[int, int], int] = {}

    def add_vertex(point) -> int:
        vertices.append(point)
        return len(vertices) - 1

    def quad(a: int, b: int, c: int, d: int) -> None:
        faces.append((a, b, c))
        faces.append((a, c, d))

    for ix in range(segments + 1):
        for iy in range(segments + 1):
            grid[(ix, iy)] = add_vertex((float(lin[ix]), float(lin[iy]), 0.0))
    for ix in range(segments):
        for iy in range(segments):
            quad(grid[(ix, iy)], grid[(ix + 1, iy)], grid[(ix + 1, iy + 1)], grid[(ix, iy + 1)])
    return np.asarray(vertices, dtype=np.float64), np.asarray(faces, dtype=np.int64)


_SCENARIOS: dict[str, Callable[[], Callable[[], None]]] = {
    "plan_50_ops": scenario_plan_50_ops,
    "plan_stress_first": scenario_plan_stress_first,
    "plan_stress_replan": scenario_plan_stress_replan,
    "simulate_20_ops": scenario_simulate_20_ops,
    "post_12k_motion": scenario_post_12k_motion,
    "render_5k_entities": scenario_render_5k_entities,
    "import3d_stl": scenario_import3d_stl,
    "import3d_step": scenario_import3d_step,
    "picking_3d": scenario_picking_3d,
    "render_3d_mesh": scenario_render_3d_mesh,
    "plan3d_feature": scenario_plan3d_feature,
}


def load_budgets() -> dict[str, float]:
    if not _BUDGET_FILE.exists():
        return {}
    return json.loads(_BUDGET_FILE.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AntCAM RC2 benchmark harness")
    parser.add_argument("--scenario", choices=sorted(_SCENARIOS), help="run a single scenario")
    parser.add_argument("--runs", type=int, default=3, help="median runs (default 3)")
    args = parser.parse_args(argv)

    budgets = load_budgets()
    names = [args.scenario] if args.scenario else sorted(_SCENARIOS)
    failures: list[str] = []
    print(f"{'scenario':<22}{'median (s)':>12}{'budget (s)':>12}{'status':>10}")
    for name in names:
        measure = _SCENARIOS[name]()  # one-time setup, excluded from timing
        median = _timeit(measure, runs=args.runs)
        budget = budgets.get(name)
        status = "ok"
        if budget is not None and median > budget:
            status = "OVER"
            failures.append(name)
        budget_text = f"{budget:.3f}" if budget is not None else "n/a"
        print(f"{name:<22}{median:>12.3f}{budget_text:>12}{status:>10}")
    if failures:
        print(f"\nBUDGET EXCEEDED: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
