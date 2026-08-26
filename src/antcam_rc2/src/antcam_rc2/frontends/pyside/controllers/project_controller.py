"""Application-level orchestration between the UI and the kernel services.

The controller owns the current project/scene/selection state, rebuilds the
neutral render graphs and forwards high-level UI actions to the kernel.  It
never renders anything: the viewport consumes ``render_scene()``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np  # noqa: F401  (used in update_simulation_view/clear_simulation)
from PySide6.QtCore import QObject, Signal

from antcam_rc2.app.application import Application
from antcam_rc2.core.databases.models import MachineProfile
from antcam_rc2.core.geometry3d.scene import SolidScene
from antcam_rc2.core.identifiers import new_id
from antcam_rc2.core.io import import_file
from antcam_rc2.core.io.scene import GeometryScene
from antcam_rc2.core.io3d import import_file_3d
from antcam_rc2.core.project.fixture_library import FixtureLibrary
from antcam_rc2.core.project.geometry_refs import create_geometry_ref
from antcam_rc2.core.project.models import (
    Fixture,
    FixtureKind,
    OperationParameters,
    OperationType,
    Project,
    Stock,
    StockOrigin,
)
from antcam_rc2.core.project.solid_refs import create_solid_ref
from antcam_rc2.core.rendering import (
    compose_scenes,
    geometry_to_scene,
    setup_to_scene,
    solid_to_scene,
    toolpath_to_scene,
)
from antcam_rc2.core.rendering.scene_graph import RenderScene
from antcam_rc2.core.simulation.voxels import VoxelGrid
from antcam_rc2.core.toolpath.models import ToolpathArtifact, ToolpathPlan
from antcam_rc2.frontends.pyside import theme
from antcam_rc2.frontends.pyside.controllers.event_bridge import EventBridge
from antcam_rc2.frontends.pyside.controllers.simulation_controller import SimulationController
from antcam_rc2.frontends.pyside.controllers.toolpath_controller import ToolpathController
from antcam_rc2.frontends.pyside.viewport.gl_viewport import GLViewport


class ProjectController(QObject):
    """Coordinates kernel services, render graphs and the viewport state."""

    project_opened = Signal()
    project_closed = Signal()
    scene_changed = Signal()
    operations_changed = Signal()
    fixtures_changed = Signal()
    fixture_library_changed = Signal()
    stock_changed = Signal()
    selection_changed = Signal()
    toolpath_changed = Signal()
    status_message = Signal(str)

    def __init__(self, application: Application, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app = application
        self._project: Project | None = None
        self._scene: GeometryScene | None = None
        self._solid_scene: SolidScene | None = None
        self._machine: MachineProfile | None = None
        self._geometry_graph = RenderScene()
        self._setup_graph = RenderScene()
        self._toolpath_graph = RenderScene()
        self._solid_graph = RenderScene()
        self._selected_operation_id: str | None = None
        self._last_plan: ToolpathPlan | None = None
        self._last_artifact: ToolpathArtifact | None = None

        self.event_bridge = EventBridge(application.event_bus, self)
        self.toolpath_controller = ToolpathController(application, self)
        self.simulation_controller = SimulationController(application, self)
        self._viewport: GLViewport | None = None
        self.toolpath_controller.plan_ready.connect(self._on_plan_ready)
        self.toolpath_controller.busy_changed.connect(self._on_plan_busy)
        self.toolpath_controller.plan_failed.connect(
            lambda message: self.status_message.emit(f"Planning failed: {message}")
        )
        # Kernel events (post-commit) refresh the UI state exactly once per mutation.
        bridge = self.event_bridge
        bridge.operation_added.connect(self._on_operations_event)
        bridge.operation_removed.connect(self._on_operations_event)
        bridge.operation_reordered.connect(self._on_operations_event)
        bridge.operation_toggled.connect(self._on_operations_event)
        bridge.stock_changed.connect(self._on_setup_event)
        bridge.fixture_added.connect(self._on_setup_event)
        bridge.fixture_removed.connect(self._on_setup_event)
        bridge.fixture_changed.connect(self._on_setup_event)
        bridge.machine_changed.connect(self._on_setup_event)

    # ------------------------------------------------------------------ state
    @property
    def project(self) -> Project | None:
        return self._project

    @property
    def scene(self) -> GeometryScene | None:
        return self._scene

    @property
    def selected_operation_id(self) -> str | None:
        return self._selected_operation_id

    @property
    def last_plan(self) -> ToolpathPlan | None:
        return self._last_plan

    @property
    def last_artifact(self) -> ToolpathArtifact | None:
        return self._last_artifact

    @property
    def services(self) -> Application:
        return self._app

    def render_scene(self) -> RenderScene:
        """The composed render graph consumed by the viewport."""
        return compose_scenes(self._setup_graph, self._geometry_graph, self._solid_graph, self._toolpath_graph)

    def clear_toolpaths(self) -> None:
        """Clear all generated toolpaths and their render graph."""
        self._last_plan = None
        self._last_artifact = None
        self._toolpath_graph = RenderScene()
        self.toolpath_changed.emit()

    def select_operation(self, operation_id: str | None) -> None:
        """Select (or clear) the active operation."""
        if operation_id == self._selected_operation_id:
            return
        self._selected_operation_id = operation_id
        self.selection_changed.emit()

    # ------------------------------------------------------------------ project
    def new_project(self, name: str, machine_id: str, stock: Stock) -> None:
        project = self._app.project_service.create_project(name, machine_id=machine_id, stock=stock)
        self._set_project(project)
        self.status_message.emit(f"Project '{name}' created")

    def open_project(self, path: Path) -> None:
        project = self._app.project_service.import_project(path)
        self._set_project(project)
        self.status_message.emit(f"Project '{project.name}' loaded — re-import its geometry to visualize")

    def save_project(self, path: Path) -> None:
        if self._project is None:
            return
        self._app.project_service.export_project(self._project.id, path)
        self.status_message.emit(f"Project saved to {path}")

    def import_geometry(self, path: Path) -> None:
        if self._project is None:
            return
        scene = import_file(path)
        self._center_geometry_in_stock(scene)
        self._app.project_service.attach_geometry(self._project.id, scene)
        self._scene = scene
        self._geometry_graph = geometry_to_scene(scene)
        self._rebuild_setup_graph()
        self.scene_changed.emit()
        self.clear_toolpaths()
        self.status_message.emit(f"Imported {path.name}")

    def import_solid(self, path: Path) -> None:
        """Import a 3D solid (STEP/STL) with positioning dialog, centered on stock."""
        if self._project is None:
            self.status_message.emit("No project open")
            return
        scene = import_file_3d(path)

        # Show positioning dialog
        from antcam_rc2.frontends.pyside.dialogs.import_solid_dialog import ImportSolidDialog

        dialog = ImportSolidDialog(self._viewport, scene=scene, stock=self._project.stock)
        if not dialog.exec():
            self.status_message.emit("3D solid import cancelled")
            return

        # Apply the offset based on the selected origin
        offset = dialog.result_offset()
        origin = dialog.result_origin()
        self._apply_solid_origin_offset(scene, self._project.stock, origin, offset)

        # Store scene and rebuild
        self._solid_scene = scene
        self._solid_graph = solid_to_scene(scene)
        if self._viewport is not None:
            self._viewport.set_solid_scene(scene)
        self._rebuild_setup_graph()
        self.scene_changed.emit()
        self.clear_toolpaths()
        self.status_message.emit(f"Imported 3D {path.name} ({scene.feature_count()} features)")

    def _apply_solid_origin_offset(
        self,
        scene: SolidScene,
        stock: Stock,
        origin: StockOrigin,
        offset: tuple[float, float, float],
    ) -> None:
        """Apply the solid position offset relative to the stock origin."""
        ox, oy, oz = offset

        # Calculate the stock origin point in world coordinates
        if origin == StockOrigin.CENTER_XY_TOP_Z:
            origin_x = stock.position_x_mm + stock.width_mm / 2.0
            origin_y = stock.position_y_mm + stock.length_mm / 2.0
            origin_z = stock.position_z_mm + stock.height_mm
        elif origin == StockOrigin.CORNER_XY_TOP_Z:
            origin_x = stock.position_x_mm
            origin_y = stock.position_y_mm
            origin_z = stock.position_z_mm + stock.height_mm
        elif origin == StockOrigin.CENTER_XY_ZERO_Z:
            origin_x = stock.position_x_mm + stock.width_mm / 2.0
            origin_y = stock.position_y_mm + stock.length_mm / 2.0
            origin_z = stock.position_z_mm
        else:  # CORNER_XY_ZERO_Z
            origin_x = stock.position_x_mm
            origin_y = stock.position_y_mm
            origin_z = stock.position_z_mm

        # Apply WCS offset
        wcs = self._project.wcs if self._project else None
        if wcs:
            origin_x += wcs.offset_x_mm
            origin_y += wcs.offset_y_mm
            origin_z += wcs.offset_z_mm

        # Translate the solid scene
        self._solid_scene = scene.translate(origin_x + ox, origin_y + oy, origin_z + oz)

    def select_solid_feature(self, body_index: int, feature_index: int) -> None:
        """Add one picked 3D feature to the active operation (like 2D picking)."""
        if self._project is None or self._solid_scene is None:
            return
        operation_id = self._selected_operation_id
        if operation_id is None:
            self.status_message.emit("Select an operation before picking 3D features")
            return
        reference = create_solid_ref(self._solid_scene, body_index, feature_index)
        self._app.project_service.replace_solid_refs(self._project.id, operation_id, (reference,))
        feature = self._solid_scene.bodies[body_index].features[feature_index]
        self.status_message.emit(f"Added 3D {feature.kind.value} to operation")

    def undo(self) -> None:
        if self._app.project_service.undo():
            self._reload_project()
            self.status_message.emit("Undo")

    def redo(self) -> None:
        if self._app.project_service.redo():
            self._reload_project()
            self.status_message.emit("Redo")

    # ------------------------------------------------------------------ setup
    def replace_stock(self, stock: Stock) -> None:
        project = self._require_project()
        self._app.project_service.replace_stock(project.id, stock)
        self._rebuild_setup_graph()
        self.stock_changed.emit()
        self.clear_toolpaths()

    def select_machine(self, machine_id: str) -> None:
        project = self._require_project()
        self._app.project_service.select_machine(project.id, machine_id)
        self._rebuild_setup_graph()
        self.clear_toolpaths()

    def add_fixture(self, fixture: Fixture) -> None:
        project = self._require_project()
        self._app.project_service.add_fixture(project.id, fixture)
        self._rebuild_setup_graph()
        self.clear_toolpaths()

    def replace_fixture(self, fixture_id: str, fixture: Fixture) -> None:
        project = self._require_project()
        self._app.project_service.replace_fixture(project.id, fixture_id, fixture)
        self._rebuild_setup_graph()

    def remove_fixture(self, fixture_id: str) -> None:
        project = self._require_project()
        self._app.project_service.remove_fixture(project.id, fixture_id)
        self._rebuild_setup_graph()
        self.clear_toolpaths()

    # ------------------------------------------------------------------ fixture library
    def import_fixture_mesh(self, path: Path) -> Fixture | None:
        """Import a STEP/STL file as a fixture.

        Computes bounding box, opens FixtureDialog pre-filled with dimensions,
        copies mesh to library on accept, and adds fixture to current project.
        Returns the created fixture or None if cancelled.
        """
        if self._project is None:
            self.status_message.emit("No project open")
            return None
        try:
            scene = import_file_3d(path)
        except Exception as exc:
            self.status_message.emit(f"Failed to import mesh: {exc}")
            return None
        bbox = scene.bounding_box()
        center = bbox.center
        fixture = Fixture(
            id=new_id("fix"),
            name=path.stem,
            kind=FixtureKind.FIXED,
            width_mm=bbox.width,
            length_mm=bbox.height,
            height_mm=bbox.depth,
            position_x_mm=-center[0],
            position_y_mm=-center[1],
            position_z_mm=-center[2],
            mesh_path=None,  # Will be set by dialog after library copy
        )
        # Import FixtureDialog here to avoid circular import
        from antcam_rc2.frontends.pyside.dialogs.fixture_dialog import FixtureDialog

        dialog = FixtureDialog(self._viewport, fixture=fixture, mesh_source=path)
        if dialog.exec():
            created = dialog.result_fixture()
            self.add_fixture(created)
            self.status_message.emit(f"Fixture '{created.name}' imported from {path.name}")
            return created
        return None

    def save_fixture_to_library(self, fixture: Fixture, mesh_source: Path | None = None) -> Fixture:
        """Save a fixture to the user library, copying mesh if provided."""
        lib = FixtureLibrary.get_default()
        saved = lib.save_fixture(fixture, mesh_source)
        self.fixture_library_changed.emit()
        self.status_message.emit(f"Fixture '{saved.name}' saved to library")
        return saved

    def load_fixture_from_library(self, fixture_id: str) -> Fixture | None:
        """Load a fixture from the user library into the current project."""
        if self._project is None:
            self.status_message.emit("No project open")
            return None
        lib = FixtureLibrary.get_default()
        fixture = lib.get_fixture(fixture_id)
        if fixture is None:
            self.status_message.emit(f"Fixture not found in library: {fixture_id}")
            return None
        self.add_fixture(fixture)
        self.status_message.emit(f"Fixture '{fixture.name}' loaded from library")
        return fixture

    # ------------------------------------------------------------------ operations
    def add_operation(
        self,
        operation_type: OperationType | str,
        *,
        tool_id: str,
        cooling_id: str,
        name: str | None = None,
    ) -> str | None:
        project = self._require_project()
        operation = self._app.project_service.add_operation(
            project.id, operation_type, tool_id=tool_id, cooling_id=cooling_id, name=name
        )
        return operation.id

    def duplicate_operation(self, operation_id: str) -> None:
        project = self._require_project()
        self._app.project_service.duplicate_operation(project.id, operation_id)

    def remove_operation(self, operation_id: str) -> None:
        project = self._require_project()
        self._app.project_service.remove_operation(project.id, operation_id)
        if self._selected_operation_id == operation_id:
            self._selected_operation_id = None
            self.selection_changed.emit()

    def toggle_operation(self, operation_id: str, enabled: bool) -> None:
        project = self._require_project()
        self._app.project_service.toggle_operation(project.id, operation_id, enabled)

    def move_operation(self, operation_id: str, new_index: int) -> None:
        project = self._require_project()
        self._app.project_service.move_operation(project.id, operation_id, new_index)

    def move_operation_by(self, operation_id: str, delta: int) -> None:
        project = self._require_project()
        operations = self._app.project_service.list_operations(project.id)
        for index, operation in enumerate(operations):
            if operation.id == operation_id:
                self._app.project_service.move_operation(project.id, operation_id, index + delta)
                break

    def update_operation_parameters(self, operation_id: str, parameters: OperationParameters) -> None:
        project = self._require_project()
        self._app.project_service.replace_operation_parameters(project.id, operation_id, parameters)

    def update_geometry_refs(self, operation_id: str, layer: str, entity_index: int) -> None:
        """Add one picked entity to an operation's geometry selection."""
        project = self._require_project()
        if self._scene is None:
            self.status_message.emit("No geometry scene attached")
            return
        reference = create_geometry_ref(self._scene, layer, entity_index)
        operations = self._app.project_service.list_operations(project.id)
        operation = next((candidate for candidate in operations if candidate.id == operation_id), None)
        if operation is None:
            return
        refs = tuple(
            reference if ref.layer_name == layer and ref.entity_index == entity_index else ref
            for ref in operation.geometry_refs
        )
        if not any(ref.entity_fingerprint == reference.entity_fingerprint for ref in operation.geometry_refs):
            refs = operation.geometry_refs + (reference,)
        self._app.project_service.replace_geometry_refs(project.id, operation_id, refs)
        self.status_message.emit(f"Added geometry to operation '{operation.name}'")

    # ------------------------------------------------------------------ toolpath
    def generate_toolpath(self) -> None:
        project = self._require_project()
        if self._scene is None:
            self.status_message.emit("Import geometry before generating a toolpath")
            return
        self.toolpath_controller.generate(project.id, self._scene)

    def bind_viewport(self, viewport: GLViewport) -> None:
        """Bind the viewport used for simulation visuals (called by MainWindow)."""
        self._viewport = viewport

    def update_simulation_view(
        self,
        mask: np.ndarray | None,
        voxel_size: float,
        marker_position: tuple[float, float, float] | None,
        _tick: int,
    ) -> None:
        """Push the simulated material mesh and tool marker to the viewport."""
        if self._viewport is None:
            return
        if mask is None:
            self._viewport.set_tool_marker(marker_position)
            return
        if self._project is None:
            return
        stock = self._project.stock
        origin = (
            stock.position_x_mm + self._project.wcs.offset_x_mm,
            stock.position_y_mm + self._project.wcs.offset_y_mm,
            stock.position_z_mm + self._project.wcs.offset_z_mm,
        )
        grid = VoxelGrid(origin=origin, voxel_size=voxel_size, shape=mask.shape, occupied=mask)
        vertices, triangles = grid.surface_mesh()
        self._viewport.set_voxel_mesh(vertices, triangles, theme.SIMULATED_MATERIAL_COLOR)
        self._viewport.set_tool_marker(marker_position)

    def clear_simulation(self) -> None:
        """Remove the simulated material mesh and tool marker from the viewport."""
        if self._viewport is not None:
            import numpy as np

            self._viewport.set_voxel_mesh(np.empty((0, 3)), np.empty((0, 3), dtype=np.int64), (1, 1, 1, 1))
            self._viewport.set_tool_marker(None)

    # ------------------------------------------------------------------ internals
    def _set_project(self, project: Project) -> None:
        self._project = project
        self._machine = self._app.catalog_repository.machine(project.machine_id)
        self._selected_operation_id = None
        self._last_plan = None
        self._last_artifact = None
        self._scene = None
        self._geometry_graph = RenderScene()
        self._toolpath_graph = RenderScene()
        self._rebuild_setup_graph()
        self.project_opened.emit()
        self.operations_changed.emit()
        self.toolpath_changed.emit()

    def _reload_project(self) -> None:
        if self._project is None:
            return
        self._project = self._app.project_service.get_project(self._project.id)
        self._machine = self._app.catalog_repository.machine(self._project.machine_id)
        self._rebuild_setup_graph()
        self.operations_changed.emit()

    def _rebuild_setup_graph(self) -> None:
        if self._project is None:
            self._setup_graph = RenderScene()
            self.scene_changed.emit()
            return
        material = self._app.catalog_repository.material(self._project.stock.material_id)
        stock_color = theme.stock_color_for_material_family(material.family)
        from antcam_rc2.core.project.fixture_library import FixtureLibrary

        fixture_lib_base = FixtureLibrary.get_default().base_dir
        self._setup_graph = setup_to_scene(
            self._project,
            machine=self._machine,
            stock_color=stock_color,
            stock_edge_color=theme.opaque(stock_color),
            fixture_color=theme.FIXTURE_COLOR_SOLID,
            fixture_outline_color=theme.FIXTURE_OUTLINE_COLOR,
            fixture_mesh_color=theme.FIXTURE_MESH_COLOR,
            work_area_color=theme.WORK_AREA_COLOR,
            origin_color=theme.ORIGIN_COLOR,
            fixture_library_base=fixture_lib_base,
        )
        self.scene_changed.emit()

    def _center_geometry_in_stock(self, scene: GeometryScene) -> None:
        """Translate the imported 2D scene so its centre matches the stock centre.

        The drawing is centred on the stock footprint (XY) while keeping Z = 0;
        this keeps both the render graph and later toolpath generation aligned
        with the workpiece instead of the drawing's bottom-left origin.
        """
        if not any(True for _ in scene.iter_entities()):
            return
        assert self._project is not None
        bbox = scene.bounding_box()
        stock = self._project.stock
        wcs = self._project.wcs
        target_x = stock.position_x_mm + wcs.offset_x_mm + stock.width_mm / 2.0
        target_y = stock.position_y_mm + wcs.offset_y_mm + stock.length_mm / 2.0
        scene.translate(target_x - bbox.center.x, target_y - bbox.center.y)

    def _on_plan_ready(self, plan: ToolpathPlan) -> None:
        self._last_plan = plan
        self._last_artifact = None
        if self._project is not None and self._scene is not None:
            self._toolpath_graph = toolpath_to_scene(plan, color_for_operation=theme.toolpath_color)
            self.toolpath_changed.emit()
        executable = plan.is_executable
        self.status_message.emit(
            f"Toolpath {'generated and executable' if executable else 'generated with errors'} "
            f"({len(plan.operations)} operations)"
        )

    def _on_plan_busy(self, busy: bool) -> None:
        self.status_message.emit("Planning toolpath..." if busy else "Planning finished")

    def _on_operations_event(self, *_args) -> None:
        self._reload_project()

    def _on_setup_event(self, *_args) -> None:
        self._reload_project()
        self.fixtures_changed.emit()

    def _require_project(self) -> Project:
        """Return the current project or raise when no project is open."""
        if self._project is None:
            raise RuntimeError("no project is open")
        return self._project
