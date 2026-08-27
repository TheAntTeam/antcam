"""Command-line entry point for AntCAM RC2 (``python -m antcam_rc2``)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from antcam_rc2 import __version__
from antcam_rc2.core.project.models import StockOrigin


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(prog="antcam-rc2", description="AntCAM RC2 — CNC CAM engine")
    parser.add_argument("--version", action="version", version=f"antcam-rc2 {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    import_parser = subparsers.add_parser("import", help="Import a 2D file (DXF/SVG)")
    import_parser.add_argument("file", help="path to the file to import")
    import_parser.add_argument("--dump", action="store_true", help="print detailed entity listing")
    subparsers.add_parser("gui", help="Launch the desktop UI (requires the PySide6 extra)")
    plan_parser = subparsers.add_parser("plan", help="Generate a neutral toolpath plan from a project and DXF/SVG")
    plan_parser.add_argument("project", help="path to an .antcam.json project document")
    plan_parser.add_argument("--geometry", required=True, help="path to the DXF/SVG geometry source")
    plan_parser.add_argument("--out", required=True, help="path for the generated toolpath JSON")
    plan_parser.add_argument(
        "--artifact", default=None, help="optional path for the versioned *.toolpath.json artifact"
    )
    plan_parser.add_argument("--clearance-z", type=float, default=5.0, help="clearance above stock top in mm")
    plan_parser.add_argument("--dump", action="store_true", help="print operation planning summary")
    simulate_parser = subparsers.add_parser("simulate", help="Run the voxel simulation for a plan")
    simulate_parser.add_argument("project", help="path to an .antcam.json project document")
    simulate_parser.add_argument("--geometry", required=True, help="path to the DXF/SVG geometry source")
    simulate_parser.add_argument("--out", required=True, help="path for the generated simulation JSON")
    simulate_parser.add_argument("--resolution", type=float, default=None, help="voxel resolution in mm")
    simulate_parser.add_argument("--clearance-z", type=float, default=5.0, help="clearance above stock top in mm")
    simulate_parser.add_argument("--dump", action="store_true", help="print event and stats summary")
    post_parser = subparsers.add_parser("post", help="Post-process a plan to G-code (.nc)")
    post_parser.add_argument("project", help="path to an .antcam.json project document")
    post_parser.add_argument("--geometry", required=True, help="path to the DXF/SVG geometry source")
    post_parser.add_argument("--post", default=None, help="post id (default: machine native_post)")
    post_parser.add_argument("--out", required=True, help="path for the generated .nc file")
    post_parser.add_argument("--clearance-z", type=float, default=5.0, help="clearance above stock top in mm")
    post_parser.add_argument("--dump", action="store_true", help="print post summary")
    demo_parser = subparsers.add_parser(
        "demo", help="Run the full import -> plan -> simulate -> post pipeline on a bundled sample"
    )
    demo_parser.add_argument(
        "--sample",
        default="mech_plate",
        choices=("mech_plate", "pcb_panel", "imperial_part"),
        help="bundled sample drawing (default: mech_plate)",
    )
    demo_parser.add_argument("--outdir", default="antcam_demo", help="output directory (default: antcam_demo)")
    demo_parser.add_argument("--post", default=None, help="post id (default: machine native_post)")
    demo_parser.add_argument("--resolution", type=float, default=1.0, help="voxel resolution in mm")
    demo_parser.add_argument("--clearance-z", type=float, default=5.0, help="clearance above stock top in mm")
    demo_parser.add_argument("--depth", type=float, default=2.0, help="cut depth in mm")
    import3d_parser = subparsers.add_parser("import3d", help="Import a 3D file (STEP/STL)")
    import3d_parser.add_argument("file", help="path to the file to import")
    import3d_parser.add_argument("--tol", type=float, default=0.05, help="tessellation tolerance in mm (STEP only)")
    import3d_parser.add_argument("--dump", action="store_true", help="print detected features")
    list3d_parser = subparsers.add_parser("list3d", help="List detected 3D features of a STEP/STL file")
    list3d_parser.add_argument("file", help="path to the file to import")
    list3d_parser.add_argument("--tol", type=float, default=0.05, help="tessellation tolerance in mm (STEP only)")
    plan3d_parser = subparsers.add_parser("plan3d", help="Auto-plan detected 3D features into a toolpath plan")
    plan3d_parser.add_argument("project", help="path to an .antcam.json project document")
    plan3d_parser.add_argument("--solid", required=True, help="path to the STEP/STL solid source")
    plan3d_parser.add_argument("--out", required=True, help="path for the generated toolpath JSON")
    plan3d_parser.add_argument("--clearance-z", type=float, default=5.0, help="clearance above feature plane in mm")
    plan3d_parser.add_argument("--depth", type=float, default=2.0, help="cut depth in mm")
    plan3d_parser.add_argument("--dump", action="store_true", help="print operation planning summary")
    gui_demo_parser = subparsers.add_parser(
        "gui-demo", help="Launch the desktop UI with a bundled sample preloaded (project + geometry + operations)"
    )
    gui_demo_parser.add_argument(
        "--sample",
        default="mech_plate",
        choices=("mech_plate", "pcb_panel", "imperial_part"),
        help="bundled sample drawing (default: mech_plate)",
    )
    gui_demo_parser.add_argument("--clearance-z", type=float, default=5.0, help="clearance above stock top in mm")
    gui_demo_parser.add_argument("--depth", type=float, default=2.0, help="cut depth in mm")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entry point, returning the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "import":
        return _run_import(args)
    if args.command == "import3d":
        return _run_import3d(args)
    if args.command == "list3d":
        return _run_list3d(args)
    if args.command == "plan3d":
        return _run_plan3d(args)
    if args.command == "plan":
        return _run_plan(args)
    if args.command == "simulate":
        return _run_simulate(args)
    if args.command == "post":
        return _run_post(args)
    if args.command == "demo":
        return _run_demo(args)
    if args.command == "gui-demo":
        return _run_gui_demo(args)
    if args.command == "gui":
        return _run_gui()
    parser.print_help()
    return 0


def _run_gui() -> int:
    """Launch the desktop UI; PySide6 is imported lazily (optional extra)."""
    try:
        from antcam_rc2.frontends.pyside.main import main
    except ImportError:  # pragma: no cover - depends on the environment
        print("error: PySide6 is not installed; install with: pip install -e 'src/antcam_rc2[gui]'", file=sys.stderr)
        return 2
    return main()


def _run_gui_demo(args) -> int:
    """Launch the desktop UI with a pre-configured project (empty, no geometry auto-loaded)."""
    import importlib.util

    if importlib.util.find_spec("PySide6") is None:  # pragma: no cover - depends on the environment
        print("error: PySide6 is not installed; install with: pip install -e 'src/antcam_rc2[gui]'", file=sys.stderr)
        return 2

    from antcam_rc2.app.application import Application
    from antcam_rc2.core.project.models import Stock, StockOrigin
    from antcam_rc2.frontends.pyside.app_window import MainWindow
    from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController
    from antcam_rc2.frontends.pyside.main import create_application

    core = Application()
    try:
        app = create_application()
        controller = ProjectController(core)
        window = MainWindow(controller)
        window.setWindowTitle(f"AntCAM RC2 — demo {args.sample}")
        controller.new_project(
            f"demo {args.sample}",
            machine_id="makera_z1",
            stock=Stock(
                width_mm=200.0,
                length_mm=200.0,
                height_mm=10.0,
                material_id="aluminum_6061",
                origin=StockOrigin.CORNER_XY_TOP_Z,
            ),
        )
        controller.status_message.emit(f"Demo {args.sample}: project ready; import geometry manually")
        window.show()
        return app.exec()
    finally:
        core.shutdown()


def _run_simulate(args) -> int:
    """Plan a project and run the deterministic voxel simulation."""
    from antcam_rc2.app.application import Application
    from antcam_rc2.core.io import import_file
    from antcam_rc2.core.simulation import SimulationSettings, Simulator
    from antcam_rc2.core.toolpath.settings import PlanningSettings

    app = Application()
    try:
        project = app.project_service.import_project(Path(args.project))
        project = app.project_service.get_project(project.id)
        scene = import_file(args.geometry)
        plan = app.toolpath_service.plan_snapshot(project, scene, PlanningSettings(clearance_z_mm=args.clearance_z))
        result = Simulator(app.catalog_repository).simulate(
            project, plan, SimulationSettings(voxel_resolution_mm=args.resolution)
        )
        report = result.report
    except FileNotFoundError as exc:
        print(f"error: file not found: {exc.filename}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    finally:
        app.shutdown()

    if args.dump:
        for event in report.events:
            print(
                f"  {event.severity.value}: {event.code.value}: {event.message} "
                f"@ {event.position.x_mm:.2f},{event.position.y_mm:.2f},{event.position.z_mm:.2f}"
            )
        stats = report.stats
        print(
            f"stats: removed={stats.removed_mm3:.1f}mm3 depth={stats.max_depth_reached_mm:.2f}mm "
            f"collisions={stats.collision_count} ops={stats.operations_simulated}/{stats.operations_skipped}"
        )
        print(f"fingerprint: {report.fingerprint()}")

    if any(event.code.value == "plan_not_executable" for event in report.events):
        print("error: toolpath plan is not executable", file=sys.stderr)
        return 1
    Path(args.out).write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return 0


def _run_post(args) -> int:
    """Plan a project and post-process it to a G-code file."""
    from antcam_rc2.app.application import Application
    from antcam_rc2.core.io import import_file
    from antcam_rc2.core.post import PostService
    from antcam_rc2.core.toolpath.settings import PlanningSettings

    app = Application()
    try:
        project = app.project_service.import_project(Path(args.project))
        project = app.project_service.get_project(project.id)
        scene = import_file(args.geometry)
        plan = app.toolpath_service.plan_snapshot(project, scene, PlanningSettings(clearance_z_mm=args.clearance_z))
        program = PostService(app.catalog_repository).post(project, plan, post_id=args.post)
    except FileNotFoundError as exc:
        print(f"error: file not found: {exc.filename}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    finally:
        app.shutdown()

    if args.dump:
        print(f"post: {program.post_id} | lines: {len(program.lines)} | motion: {program.motion_line_count}")
        for line in program.lines[:6]:
            print(f"  {line}")
        print(f"fingerprint: {program.fingerprint()}")

    Path(args.out).write_text(program.text() + "\n", encoding="utf-8")
    return 0


def _add_demo_operations(project_service, project_id: str, scene, depth: float) -> int:
    """Auto-add profiling/pocketing/drill operations for a demo scene.

    Returns the number of operations added (0 when nothing usable was found).
    """
    from antcam_rc2.core.geometry.curves import Circle
    from antcam_rc2.core.geometry.paths import Contour
    from antcam_rc2.core.geometry.paths import Path as GeomPath
    from antcam_rc2.core.project.geometry_refs import create_geometry_ref
    from antcam_rc2.core.project.models import OperationParameters, OperationType

    added = 0
    for layer_index, layer in enumerate(scene.layers):
        for entity_index, entity in enumerate(layer.entities):
            if isinstance(entity, Circle):
                project_service.add_operation(
                    project_id,
                    OperationType.DRILL,
                    tool_id="drill_2",
                    cooling_id="aerodust",
                    geometry_refs=(create_geometry_ref(scene, layer.name, entity_index),),
                    operation_parameters=OperationParameters(depth_mm=depth),
                )
                added += 1
            elif (isinstance(entity, Contour) and entity.closed) or isinstance(entity, GeomPath):
                kind = OperationType.PROFILING if layer_index == 0 else OperationType.POCKETING
                project_service.add_operation(
                    project_id,
                    kind,
                    tool_id="end_mill_3_175_2f",
                    cooling_id="aerodust",
                    geometry_refs=(create_geometry_ref(scene, layer.name, entity_index),),
                    operation_parameters=OperationParameters(depth_mm=depth, stepdown_mm=1.0),
                )
                added += 1
            if added >= 4:
                return added
    return added


def _demo_sample_path(sample: str) -> Path:
    samples_dir = Path(__file__).parent / "samples"
    extensions = {"mech_plate": "dxf", "pcb_panel": "svg", "imperial_part": "dxf"}
    return samples_dir / f"{sample}.{extensions[sample]}"


def _positioned_demo_scene(scene, margin_mm: float = 4.0):
    """Translate a scene so its bbox starts at ``(margin, margin)``.

    The profiling entry/exit arc extends roughly one tool radius beyond the
    part edge; placing the part flush at the work-area origin would push the
    tool tip to negative coordinates and trigger ``outside_work_area``.  A
    margin keeps the whole toolpath inside the machine envelope (demo-only
    presentation, the engine checks remain untouched).
    """
    from antcam_rc2.core.geometry.transform import Affine2D, apply
    from antcam_rc2.core.io.scene import GeometryScene

    box = scene.bounding_box()
    if box.width == 0.0 and box.height == 0.0:
        return scene
    translation = Affine2D.translate(margin_mm - box.min_x, margin_mm - box.min_y)
    if abs(translation.e) < 1e-9 and abs(translation.f) < 1e-9:
        return scene
    positioned = GeometryScene(
        source=scene.source.model_copy(deep=True),
        units=scene.units,
        tolerance_mm=scene.tolerance_mm,
        diagnostics=scene.diagnostics,
    )
    for layer in scene.layers:
        for entity in layer.entities:
            positioned.add_entity(apply(translation, entity), layer.name)
    return positioned


def _run_demo(args) -> int:
    """Run the full pipeline on a bundled sample: import -> project -> plan -> simulate -> post."""
    from antcam_rc2.app.application import Application
    from antcam_rc2.core.io import import_file
    from antcam_rc2.core.post import PostService
    from antcam_rc2.core.project.models import Stock
    from antcam_rc2.core.simulation import SimulationSettings, Simulator
    from antcam_rc2.core.toolpath.settings import PlanningSettings

    sample_path = _demo_sample_path(args.sample)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    app = Application()
    try:
        scene = _positioned_demo_scene(import_file(sample_path))
        box = scene.bounding_box()
        project = app.project_service.create_project(
            f"demo {args.sample}",
            machine_id="makera_z1",
            stock=Stock(
                width_mm=max(box.width, 1.0) + 10.0,
                length_mm=max(box.height, 1.0) + 10.0,
                height_mm=10.0,
                material_id="aluminum_6061",
                origin=StockOrigin.CORNER_XY_TOP_Z,
            ),
        )
        app.project_service.attach_geometry(project.id, scene)
        added = _add_demo_operations(app.project_service, project.id, scene, args.depth)
        if added == 0:
            print("error: no profile/circle entities found in the sample", file=sys.stderr)
            return 1

        project = app.project_service.get_project(project.id)
        settings = PlanningSettings(clearance_z_mm=args.clearance_z)
        plan = app.toolpath_service.plan_snapshot(project, scene, settings)
        simulation = (
            Simulator(app.catalog_repository)
            .simulate(project, plan, SimulationSettings(voxel_resolution_mm=args.resolution))
            .report
        )
        program = PostService(app.catalog_repository).post(project, plan, post_id=args.post)
    except Exception as exc:  # noqa: BLE001 - demo should always fail gracefully
        print(f"error: {exc}", file=sys.stderr)
        return 3
    finally:
        app.shutdown()

    (outdir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    (outdir / "report.json").write_text(simulation.model_dump_json(indent=2), encoding="utf-8")
    (outdir / "output.nc").write_text(program.text() + "\n", encoding="utf-8")

    print(f"sample:      {args.sample} ({scene.source.format})")
    print(f"operations:  {len(plan.operations)} ({added} auto-added)")
    print(f"plan:        {'executable' if plan.is_executable else 'NOT executable'}  ({len(plan.operations)} ops)")
    stats = simulation.stats
    print(
        f"simulation:  removed={stats.removed_mm3:.1f} mm3  depth={stats.max_depth_reached_mm:.2f} mm  "
        f"collisions={stats.collision_count}"
    )
    print(f"post:        {program.post_id}  motion={program.motion_line_count} lines")
    for operation, result in zip(project.operations, plan.operations, strict=False):
        print(f"  - {operation.operation_type.value}: {result.status.value}")
    print(f"outputs:     {outdir / 'plan.json'}, {outdir / 'report.json'}, {outdir / 'output.nc'}")
    return 0 if plan.is_executable else 1


def _run_import(args) -> int:
    from antcam_rc2.core.io import import_file

    try:
        scene = import_file(args.file)
    except FileNotFoundError:
        print(f"error: file not found: {args.file}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"format:    {scene.source.format}")
    print(f"units:     {scene.units.value}")
    print(f"layers:    {', '.join(layer.name for layer in scene.layers) or '(none)'}")
    print(f"entities:  {len(scene.entities())}")
    box = scene.bounding_box()
    print(
        f"bbox:      [{box.min_x:.3f}, {box.min_y:.3f}] .. [{box.max_x:.3f}, {box.max_y:.3f}]"
        f"  ({box.width:.3f} x {box.height:.3f})"
    )
    print(f"diagnostics: {scene.diagnostics.summary()}")

    if args.dump:
        for layer_name, entity in scene.iter_entities():
            kind = type(entity).__name__
            length = getattr(entity, "length", lambda: None)()
            print(f"  [{layer_name}] {kind} length={length:.3f}")

    for message in scene.diagnostics.warnings:
        print(f"warning: {message}", file=sys.stderr)
    for message in scene.diagnostics.errors:
        print(f"error: {message}", file=sys.stderr)

    return 1 if scene.diagnostics.has_errors else 0


def _run_import3d(args) -> int:
    from antcam_rc2.core.io3d import import_file_3d

    try:
        scene = import_file_3d(args.file)
    except FileNotFoundError:
        print(f"error: file not found: {args.file}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"format:    {scene.source.format}")
    print(f"bodies:    {len(scene.bodies)}")
    total_faces = sum(body.mesh.face_count for body in scene.bodies)
    print(f"triangles: {total_faces}")
    print(f"features:  {scene.feature_count()}")
    for message in scene.warnings:
        print(f"warning: {message}", file=sys.stderr)
    for message in scene.errors:
        print(f"error: {message}", file=sys.stderr)

    if args.dump:
        for body_index, feature in scene.iter_features():
            extra = ""
            if feature.kind.value == "hole" and feature.radius is not None:
                extra = f" radius={feature.radius:.2f}"
            print(
                f"  body {body_index}[{feature.feature_index}] {feature.kind.value}"
                f" z={feature.plane_z_mm:.2f} facing={feature.facing}{extra}"
            )
    return 1 if scene.has_errors() else 0


def _run_list3d(args) -> int:
    from antcam_rc2.core.io3d import import_file_3d

    try:
        scene = import_file_3d(args.file)
    except FileNotFoundError:
        print(f"error: file not found: {args.file}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for body_index, feature in scene.iter_features():
        extra = ""
        if feature.kind.value == "hole" and feature.radius is not None:
            extra = f" radius={feature.radius:.2f}"
        print(f"body {body_index}[{feature.feature_index}] {feature.kind.value} z={feature.plane_z_mm:.2f}{extra}")
    return 0


def _add_3d_operations(project_service, project_id: str, solid, depth: float) -> int:
    """Auto-add drill/pocketing operations for detected 3D features."""
    from antcam_rc2.core.geometry3d.scene import FeatureKind
    from antcam_rc2.core.project.models import OperationParameters, OperationType
    from antcam_rc2.core.project.solid_refs import create_solid_ref

    added = 0
    for body_index, body in enumerate(solid.bodies):
        for feature_index, feature in enumerate(body.features):
            if feature.kind is FeatureKind.HOLE:
                operation_type = OperationType.DRILL
                tool_id = "drill_2"
            elif feature.kind is FeatureKind.FACE_PLANAR and feature.facing:
                operation_type = OperationType.POCKETING
                tool_id = "end_mill_3_175_2f"
            else:
                continue
            reference = create_solid_ref(solid, body_index, feature_index)
            project_service.add_operation(
                project_id,
                operation_type,
                tool_id=tool_id,
                cooling_id="aerodust",
                solid_refs=(reference,),
                operation_parameters=OperationParameters(depth_mm=depth, stepdown_mm=1.0),
            )
            added += 1
    return added


def _run_plan3d(args) -> int:
    from pathlib import Path

    from antcam_rc2.app.application import Application
    from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
    from antcam_rc2.core.io3d import import_file_3d
    from antcam_rc2.core.toolpath.settings import PlanningSettings

    app = Application()
    try:
        project = app.project_service.import_project(Path(args.project))
        project = app.project_service.get_project(project.id)
        solid = import_file_3d(args.solid)
        added = _add_3d_operations(app.project_service, project.id, solid, args.depth)
        if added == 0:
            print("error: no drillable/pocketable 3D features found", file=sys.stderr)
            return 1
        project = app.project_service.get_project(project.id)
        empty_scene = GeometryScene(source=SourceInfo(format="dxf"))
        plan = app.toolpath_service.plan_snapshot(
            project,
            empty_scene,
            PlanningSettings(clearance_z_mm=args.clearance_z),
            solid_scene=solid,
        )
    except FileNotFoundError as exc:
        print(f"error: file not found: {exc.filename}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    finally:
        app.shutdown()

    if args.dump:
        for result in plan.operations:
            feeds = result.feeds_speeds
            feed_text = f"rpm={feeds.rpm:.0f} feed={feeds.cut_feed_mm_min:.1f}" if feeds is not None else "-"
            print(f"{result.operation_type.value}: {result.status.value} passes={result.pass_count} {feed_text}")
        print(f"fingerprint: {plan.fingerprint()}")

    if not plan.is_executable:
        print("error: toolpath plan is not executable", file=sys.stderr)
        return 1
    Path(args.out).write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return 0


def _run_plan(args) -> int:
    from antcam_rc2.app.application import Application
    from antcam_rc2.core.io import import_file
    from antcam_rc2.core.toolpath.settings import PlanningSettings

    app = Application()
    try:
        project = app.project_service.import_project(Path(args.project))
        scene = import_file(args.geometry)
        plan = app.toolpath_service.plan_project(
            project.id,
            scene,
            PlanningSettings(clearance_z_mm=args.clearance_z),
        )
    except FileNotFoundError as exc:
        print(f"error: file not found: {exc.filename}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    finally:
        app.shutdown()

    if args.dump:
        for result in plan.operations:
            feeds = result.feeds_speeds
            feed_text = (
                f"rpm={feeds.rpm:.0f} feed={feeds.cut_feed_mm_min:.1f} plunge={feeds.plunge_feed_mm_min:.1f}"
                if feeds is not None
                else "-"
            )
            print(
                f"{result.operation_id}: {result.status.value} passes={result.pass_count} "
                f"cut={result.cut_length_mm:.2f}mm {feed_text}"
            )
            for diagnostic in result.diagnostics:
                print(f"  {diagnostic.severity.value}: {diagnostic.code}: {diagnostic.message}")
        print(f"fingerprint: {plan.fingerprint()}")

    if not plan.is_executable:
        print("error: toolpath plan is not executable", file=sys.stderr)
        return 1
    Path(args.out).write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    if args.artifact:
        artifact = app.toolpath_service.export_artifact(
            project.id, scene, PlanningSettings(clearance_z_mm=args.clearance_z)
        )
        Path(args.artifact).write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
