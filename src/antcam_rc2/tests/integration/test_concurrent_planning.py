"""Concurrency: read-only planning/simulation from multiple threads is deterministic."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from antcam_rc2.app.application import Application
from antcam_rc2.core.feeds_speeds.calculator import FeedsSpeedsCalculator
from antcam_rc2.core.geometry.curves import Circle, LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.operations.registry import build_standard_registry
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import OperationParameters, OperationType, Stock
from antcam_rc2.core.simulation.models import SimulationSettings
from antcam_rc2.core.simulation.simulator import Simulator
from antcam_rc2.core.toolpath.service import PlanningSettings, ToolpathService

_THREADS = 4
_REPEATS = 3


def make_scene() -> GeometryScene:
    scene = GeometryScene(source=SourceInfo(format="dxf", path="concurrent.dxf"))
    points = [Point2(0.0, 0.0), Point2(20.0, 0.0), Point2(20.0, 10.0), Point2(0.0, 10.0)]
    scene.add_entity(
        Contour([LineSegment(a, b) for a, b in zip(points, points[1:] + [points[0]], strict=True)]),
        "profile",
    )
    scene.add_entity(Circle(Point2(10.0, 5.0), 1.5), "holes")
    return scene


def _setup(application: Application) -> tuple[str, GeometryScene]:
    project = application.project_service.create_project(
        "concurrent",
        machine_id="makera_z1",
        stock=Stock(width_mm=30.0, length_mm=20.0, height_mm=5.0, material_id="aluminum_6061"),
    )
    scene = make_scene()
    application.project_service.attach_geometry(project.id, scene)
    application.project_service.add_operation(
        project.id,
        OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "profile", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0, stepdown_mm=1.0),
    )
    application.project_service.add_operation(
        project.id,
        OperationType.DRILL,
        tool_id="drill_2",
        cooling_id="aerodust",
        geometry_refs=(create_geometry_ref(scene, "holes", 0),),
        operation_parameters=OperationParameters(depth_mm=2.0, stepdown_mm=1.0),
    )
    return project.id, scene


def test_plan_snapshot_from_multiple_threads_has_identical_fingerprints(
    application: Application,
) -> None:
    project_id, scene = _setup(application)
    snapshot = application.project_service.get_project(project_id)
    service = ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )
    settings = PlanningSettings(clearance_z_mm=5.0)

    def worker(_: int) -> str:
        results = [service.plan_snapshot(snapshot, scene, settings).fingerprint() for _ in range(_REPEATS)]
        return ",".join(results)

    with ThreadPoolExecutor(max_workers=_THREADS) as pool:
        fingerprints = list(pool.map(worker, range(_THREADS)))

    assert len(set(fingerprints)) == 1, "concurrent plans diverged"
    assert fingerprints[0].split(",")[0] == fingerprints[0].split(",")[-1]


def test_simulate_from_multiple_threads_has_identical_removed_volume(
    application: Application,
) -> None:
    project_id, scene = _setup(application)
    snapshot = application.project_service.get_project(project_id)
    service = ToolpathService(
        project_service=application.project_service,
        catalog_repository=application.catalog_repository,
        registry=build_standard_registry(),
        feeds_speeds=FeedsSpeedsCalculator(),
    )
    plan = service.plan_snapshot(snapshot, scene, PlanningSettings(clearance_z_mm=5.0))
    assert plan.is_executable
    settings = SimulationSettings(voxel_resolution_mm=1.0)

    def worker(_: int) -> tuple[int, str]:
        sim = Simulator(application.catalog_repository)
        report = sim.simulate(snapshot, plan, settings).report
        return report.stats.removed_voxels, report.fingerprint()

    with ThreadPoolExecutor(max_workers=_THREADS) as pool:
        results: list[tuple[int, str]] = list(pool.map(worker, range(_THREADS)))

    assert len({volume for volume, _ in results}) == 1
    assert len({fp for _, fp in results}) == 1
    assert results[0][0] > 0
