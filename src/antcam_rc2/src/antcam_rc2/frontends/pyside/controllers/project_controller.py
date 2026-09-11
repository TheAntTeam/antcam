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
)
from antcam_rc2.core.project.solid_refs import create_solid_ref
from antcam_rc2.core.project.stock_geometry import stock_min_corner
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
from antcam_rc2.frontends.pyside.controllers.geometry_import_controller import GeometryImportController
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
        self._selected_solid_features: set[tuple[int, int]] = set()
        self._last_plan: ToolpathPlan | None = None
        self._last_artifact: ToolpathArtifact | None = None

        self.event_bridge = EventBridge(application.event_bus, self)
        self.toolpath_controller = ToolpathController(application, self)
        self.simulation_controller = SimulationController(application, self)
        self.geometry_import_controller = GeometryImportController(application, self)
        self._viewport: GLViewport | None = None
        self.toolpath_controller.plan_ready.connect(self._on_plan_ready)
        self.toolpath_controller.busy_changed.connect(self._on_plan_busy)
        self.toolpath_controller.plan_failed.connect(
            lambda message: self.status_message.emit(f"Planning failed: {message}")
        )
        # Geometry import signals
        self.geometry_import_controller.geometry_imported.connect(self._on_geometry_imported)
        self.geometry_import_controller.solid_imported.connect(self._on_solid_imported)
        # Modeless solid placement dialog state (same dialog for import and edit, live preview, viewport navigable)
        self._solid_dialog = None  # type: ignore[var-annotated]
        self._solid_dialog_scene: SolidScene | None = None
        self._solid_dialog_is_import: bool = False
        self._solid_dialog_prev_scene: SolidScene | None = None
        self._solid_dialog_prev_project: Project | None = None
        # Modeless geometry placement dialog state
        self._geometry_dialog = None  # type: ignore[var-annotated]
        self._geometry_dialog_scene: GeometryScene | None = None
        self._geometry_dialog_is_import: bool = False
        self._geometry_dialog_prev_scene: GeometryScene | None = None
        self._geometry_dialog_prev_project: Project | None = None
        self.geometry_import_controller.fixture_loaded.connect(self._on_fixture_loaded)
        self.geometry_import_controller.import_failed.connect(
            lambda message: self.status_message.emit(f"Import failed: {message}")
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
        self.status_message.emit(f"Importing {path.name}...")
        self.geometry_import_controller.import_geometry(path)

    def _on_geometry_imported(self, scene: GeometryScene) -> None:
        if self._project is None:
            return
        # Open placement dialog (same for import and edit) with live preview
        self._open_geometry_placement_dialog(scene, placement=None, is_import=True)

    def edit_geometry_placement(self) -> None:
        """Open shared placement dialog for already-attached geometry (modeless, live preview)."""
        if self._project is None or self._scene is None:
            self.status_message.emit("No 2D geometry to edit")
            return
        placement = self._project.geometry_placement
        self._open_geometry_placement_dialog(self._scene, placement=placement, is_import=False)

    def _open_geometry_placement_dialog(
        self, scene: GeometryScene, *, placement: object | None, is_import: bool
    ) -> None:
        from antcam_rc2.core.project.models import GeometryPlacement as _GP
        from antcam_rc2.frontends.pyside.dialogs.import_geometry_dialog import ImportGeometryDialog

        if self._project is None:
            return
        if self._geometry_dialog is not None:
            try:
                self._geometry_dialog.close()
            except Exception:
                pass
            self._geometry_dialog = None
        typed_placement: _GP | None = placement if isinstance(placement, _GP) else None  # type: ignore[assignment]
        dialog = ImportGeometryDialog(
            self._viewport, scene=scene, stock=self._project.stock, wcs=self._project.wcs, placement=typed_placement
        )
        dialog.setModal(False)
        self._geometry_dialog = dialog
        self._geometry_dialog_scene = scene
        self._geometry_dialog_is_import = is_import
        self._geometry_dialog_prev_scene = self._scene
        self._geometry_dialog_prev_project = self._project

        initial = dialog.result_placement()
        if is_import:
            self._scene = scene
        self._geometry_graph = geometry_to_scene(scene, initial, self._project.stock, self._project.wcs)
        self.scene_changed.emit()

        def _on_preview_changed(obj: object) -> None:
            try:
                typed: _GP | None = obj if isinstance(obj, _GP) or obj is None else None  # type: ignore[no-redef]
                if typed is None and obj is not None:
                    return
                # Need current project stock/wcs (may have changed? use stored)
                proj = self._project if self._project is not None else self._geometry_dialog_prev_project
                if proj is None:
                    return
                self._geometry_graph = geometry_to_scene(scene, typed, proj.stock, proj.wcs)
                self.scene_changed.emit()
            except Exception:
                pass

        def _on_finished(result: int) -> None:
            dlg = self._geometry_dialog
            if dlg is None:
                return
            try:
                dlg.placement_changed.disconnect(_on_preview_changed)
            except Exception:
                pass
            try:
                dlg.finished.disconnect(_on_finished)
            except Exception:
                pass
            self._geometry_dialog = None
            accepted = result == 1  # QDialog.Accepted
            if not accepted:
                self._scene = self._geometry_dialog_prev_scene
                self._project = self._geometry_dialog_prev_project  # type: ignore[assignment]
                self._rebuild_geometry_graph()
                self.scene_changed.emit()
                self.status_message.emit("2D geometry import cancelled" if is_import else "2D placement edit cancelled")
                return
            final_placement = dlg.result_placement()
            if is_import:
                assert self._geometry_dialog_prev_project is not None
                self._app.project_service.attach_geometry(
                    self._geometry_dialog_prev_project.id, scene, final_placement
                )
                self._project = self._app.project_service.get_project(self._geometry_dialog_prev_project.id)
                self._scene = scene
            else:
                assert self._project is not None
                pid = self._project.id
                pid = self._geometry_dialog_prev_project.id if self._geometry_dialog_prev_project is not None else pid  # type: ignore[union-attr]
                self._app.project_service.replace_geometry_placement(pid, final_placement)
                self._project = self._app.project_service.get_project(pid)
            self._rebuild_geometry_graph()
            self._rebuild_setup_graph()
            self.scene_changed.emit()
            self.clear_toolpaths()
            if is_import:
                self.status_message.emit(f"Imported {scene.source.path} ({len(list(scene.iter_entities()))} entities)")
            else:
                self.status_message.emit("2D placement updated")

        dialog.placement_changed.connect(_on_preview_changed)
        dialog.finished.connect(_on_finished)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _rebuild_geometry_graph(self) -> None:
        """Rebuild geometry render graph from persisted placement (lazy apply)."""
        if self._scene is None or self._project is None:
            self._geometry_graph = RenderScene()
            return
        placement = self._project.geometry_placement
        self._geometry_graph = geometry_to_scene(self._scene, placement, self._project.stock, self._project.wcs)

    def import_solid(self, path: Path) -> None:
        """Import a 3D solid (STEP/STL) with positioning dialog, centered on stock."""
        if self._project is None:
            self.status_message.emit("No project open")
            return

        # First, we need to do a quick import to show the dialog with the solid
        # We'll do a fast import (without heavy tessellation for display)
        # Actually, the dialog needs the scene - let's do a quick import in background
        # For now, we'll do the import in background and show dialog after
        self.status_message.emit(f"Loading {path.name}...")
        self.geometry_import_controller.import_solid(path)

    def _on_solid_imported(self, scene: SolidScene) -> None:
        if self._project is None:
            return
        # Use the shared modeless placement dialog (same as Edit).
        # Viewport stays navigable (orbit/pan/zoom) while the dialog is open.
        self._open_solid_placement_dialog(scene, placement=None, is_import=True)

    def edit_solid_placement(self) -> None:
        """Open the shared placement dialog for the already-attached solid (modeless, live preview)."""
        if self._project is None or self._solid_scene is None:
            self.status_message.emit("No 3D solid to edit")
            return
        placement = self._project.solid_placement
        self._open_solid_placement_dialog(self._solid_scene, placement=placement, is_import=False)

    def _open_solid_placement_dialog(
        self, scene: SolidScene, *, placement: object | None, is_import: bool
    ) -> None:
        from antcam_rc2.core.project.models import SolidPlacement as _SP
        from antcam_rc2.core.project.solid_placement import apply_placement
        from antcam_rc2.frontends.pyside.dialogs.import_solid_dialog import ImportSolidDialog

        if self._project is None:
            return
        # Close previous dialog if still open.
        if self._solid_dialog is not None:
            try:
                self._solid_dialog.close()
            except Exception:
                pass
            self._solid_dialog = None

        typed_placement: _SP | None = placement if isinstance(placement, _SP) else None  # type: ignore[assignment]
        dialog = ImportSolidDialog(
            self._viewport, scene=scene, stock=self._project.stock, wcs=self._project.wcs, placement=typed_placement
        )
        dialog.setModal(False)
        # Keep viewport navigable; dialog is a separate non-modal window.
        self._solid_dialog = dialog
        self._solid_dialog_scene = scene
        self._solid_dialog_is_import = is_import
        self._solid_dialog_prev_scene = self._solid_scene
        self._solid_dialog_prev_project = self._project

        # Show initial placement immediately (live preview without persisting yet).
        initial = dialog.result_placement()
        if is_import:
            # New solid replaces previous one immediately in preview; clear old highlight.
            self._selected_solid_features.clear()
            self._solid_scene = scene
        self._solid_graph = solid_to_scene(scene, placement=initial, selected=self._selected_solid_features or None)
        if self._viewport is not None:
            self._viewport.set_solid_scene(apply_placement(scene, initial))
        self.scene_changed.emit()

        def _on_preview_changed(obj: object) -> None:
            try:
                typed: _SP | None = obj if isinstance(obj, _SP) or obj is None else None  # type: ignore[no-redef]
                if typed is None and obj is not None:
                    return
                sel = self._selected_solid_features or None
                self._solid_graph = solid_to_scene(scene, placement=typed, selected=sel)
                if self._viewport is not None:
                    self._viewport.set_solid_scene(apply_placement(scene, typed))
                self.scene_changed.emit()
            except Exception:
                pass

        def _on_finished(result: int) -> None:
            dlg = self._solid_dialog
            if dlg is None:
                return
            try:
                dlg.placement_changed.disconnect(_on_preview_changed)
            except Exception:
                pass
            try:
                dlg.finished.disconnect(_on_finished)
            except Exception:
                pass
            self._solid_dialog = None
            accepted = result == 1  # QDialog.Accepted
            if not accepted:
                # Revert preview to previous persisted state.
                self._solid_scene = self._solid_dialog_prev_scene
                self._project = self._solid_dialog_prev_project  # type: ignore[assignment]
                self._rebuild_solid_graph()
                self.scene_changed.emit()
                self.status_message.emit("3D solid import cancelled" if is_import else "3D placement edit cancelled")
                return
            final_placement = dlg.result_placement()
            if is_import:
                assert self._solid_dialog_prev_project is not None
                self._app.project_service.attach_solid(self._solid_dialog_prev_project.id, scene, final_placement)
                self._project = self._app.project_service.get_project(self._solid_dialog_prev_project.id)
                self._solid_scene = scene
            else:
                assert self._project is not None
                pid = self._project.id
                # _project may have been mutated during preview revert; re-fetch current
                pid = self._solid_dialog_prev_project.id if self._solid_dialog_prev_project is not None else pid  # type: ignore[union-attr]
                self._app.project_service.replace_solid_placement(pid, final_placement)
                self._project = self._app.project_service.get_project(pid)
            self._rebuild_solid_graph()
            self._rebuild_setup_graph()
            self.scene_changed.emit()
            self.clear_toolpaths()
            if is_import:
                self.status_message.emit(f"Imported 3D {scene.source.path} ({scene.feature_count()} features)")
            else:
                self.status_message.emit("3D placement updated")

        dialog.placement_changed.connect(_on_preview_changed)
        dialog.finished.connect(_on_finished)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _rebuild_solid_graph(self) -> None:
        """Rebuild the solid render graph from the persisted placement (lazy apply)."""
        if self._solid_scene is None or self._project is None:
            self._solid_graph = RenderScene()
            if self._viewport is not None:
                self._viewport.set_solid_scene(None)
            return
        placement = self._project.solid_placement
        self._solid_graph = solid_to_scene(
            self._solid_scene, placement=placement, selected=self._selected_solid_features or None
        )
        if self._viewport is not None:
            from antcam_rc2.core.project.solid_placement import apply_placement

            placed = apply_placement(self._solid_scene, placement) if placement is not None else self._solid_scene
            self._viewport.set_solid_scene(placed)

    @property
    def selected_solid_features(self) -> set[tuple[int, int]]:
        """Currently highlighted 3D features (body_index, feature_index)."""
        return set(self._selected_solid_features)

    def set_selected_solid_features(self, features: set[tuple[int, int]]) -> None:
        """Replace highlighted features and update selection + viewport highlight."""
        if self._solid_scene is None:
            self._selected_solid_features = set()
            self._rebuild_solid_graph()
            self.scene_changed.emit()
            return
        # Filter to valid indices
        valid: set[tuple[int, int]] = set()
        for bi, fi in features:
            if 0 <= bi < len(self._solid_scene.bodies) and 0 <= fi < len(self._solid_scene.bodies[bi].features):
                valid.add((bi, fi))
        if valid == self._selected_solid_features:
            return
        self._selected_solid_features = valid
        self._rebuild_solid_graph()
        self.scene_changed.emit()
        # Sync operation refs when an operation is active
        if self._selected_operation_id is not None and valid:
            self._sync_solid_refs_to_operation()

    def toggle_solid_feature(self, body_index: int, feature_index: int) -> None:
        """Toggle one feature in the highlight set (list or viewport picking)."""
        key = (body_index, feature_index)
        if key in self._selected_solid_features:
            self._selected_solid_features.remove(key)
        else:
            self._selected_solid_features.add(key)
        self._rebuild_solid_graph()
        self.scene_changed.emit()
        if self._selected_operation_id is not None:
            self._sync_solid_refs_to_operation()

    def clear_selected_solid_features(self) -> None:
        """Clear highlight without emitting refs."""
        if not self._selected_solid_features:
            return
        self._selected_solid_features.clear()
        self._rebuild_solid_graph()
        self.scene_changed.emit()

    def _sync_solid_refs_to_operation(self) -> None:
        """Push current highlight set as solid_refs on the active operation."""
        if self._project is None or self._solid_scene is None or self._selected_operation_id is None:
            return
        refs = tuple(
            create_solid_ref(self._solid_scene, bi, fi) for bi, fi in sorted(self._selected_solid_features)
        )
        self._app.project_service.replace_solid_refs(self._project.id, self._selected_operation_id, refs)

    def select_solid_feature(self, body_index: int, feature_index: int) -> None:
        """Toggle one picked 3D feature (viewport picking)."""
        if self._project is None or self._solid_scene is None:
            return
        operation_id = self._selected_operation_id
        if operation_id is None:
            # Still highlight even without active operation
            self.toggle_solid_feature(body_index, feature_index)
            self.status_message.emit("Select an operation to bind 3D features")
            return
        self.toggle_solid_feature(body_index, feature_index)
        feature = self._solid_scene.bodies[body_index].features[feature_index]
        action = "Added" if (body_index, feature_index) in self._selected_solid_features else "Removed"
        self.status_message.emit(f"{action} 3D {feature.kind.value} {'to' if action=='Added' else 'from'} operation")

    def remove_solid_completely(self) -> None:
        """Remove attached solid, all its refs and toolpaths (single source of truth)."""
        if self._project is None:
            return
        self._app.project_service.detach_solid(self._project.id)
        self._selected_solid_features.clear()
        self._reload_project()
        self.scene_changed.emit()
        self.clear_toolpaths()
        self.status_message.emit("3D solid removed")

    def remove_geometry_completely(self) -> None:
        """Remove attached 2D geometry, all its refs and toolpaths."""
        if self._project is None:
            return
        self._app.project_service.detach_geometry(self._project.id)
        self._scene = None
        self._geometry_graph = RenderScene()
        self._reload_project()
        self._rebuild_geometry_graph()
        self.scene_changed.emit()
        self.clear_toolpaths()
        self.status_message.emit("2D geometry removed")

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
        # Default position: offset (10, 10, 0) from stock minimum corner
        stock_min_x, stock_min_y, stock_min_z = stock_min_corner(self._project.stock, self._project.wcs)
        fixture = Fixture(
            id=new_id("fix"),
            name=path.stem,
            kind=FixtureKind.FIXED,
            width_mm=bbox.width,
            length_mm=bbox.height,
            height_mm=bbox.depth,
            position_x_mm=10.0,
            position_y_mm=10.0,
            position_z_mm=0.0,
            mesh_path=None,  # Will be set by dialog after library copy
        )
        # Import FixtureDialog here to avoid circular import
        from antcam_rc2.frontends.pyside.dialogs.fixture_dialog import FixtureDialog

        dialog = FixtureDialog(self._viewport, fixture=fixture, mesh_source=path, stock=self._project.stock, wcs=self._project.wcs)
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
        self.status_message.emit("Loading fixture from library...")
        self.geometry_import_controller.load_fixture(fixture_id)
        return None  # Actual fixture loaded asynchronously via _on_fixture_loaded

    def _on_fixture_loaded(self, fixture: Fixture) -> None:
        if self._project is None:
            return
        self.add_fixture(fixture)
        self.status_message.emit(f"Fixture '{fixture.name}' loaded from library")

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
        if self._scene is None and self._solid_scene is None:
            self.status_message.emit("Import geometry before generating a toolpath")
            return
        scene = self._scene
        if scene is not None and project.geometry_placement is not None:
            from antcam_rc2.core.project.geometry_placement import apply_placement as _apply_geo

            scene = _apply_geo(scene, project.geometry_placement, project.stock, project.wcs)
        if scene is None:
            from antcam_rc2.core.io.diagnostics import ImportDiagnostics
            from antcam_rc2.core.io.scene import GeometryScene, SourceInfo

            scene = GeometryScene(
                source=SourceInfo(format="dxf", path=""),
                units=project.units,
                layers=(),
                diagnostics=ImportDiagnostics(),
                tolerance_mm=1e-6,
            )
        placed_solid = None
        if self._solid_scene is not None:
            from antcam_rc2.core.project.solid_placement import apply_placement

            placed_solid = apply_placement(self._solid_scene, project.solid_placement)
        self.toolpath_controller.generate(project.id, scene, solid_scene=placed_solid)

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
        origin = stock_min_corner(stock, self._project.wcs)
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
        self._selected_solid_features.clear()
        self._last_plan = None
        self._last_artifact = None
        self._scene = self._app.project_service.get_geometry_scene(project.id)  # type: ignore[assignment]
        # Restore transient solid from service (if any) and rebuild its graph.
        self._solid_scene = self._app.project_service.get_solid_scene(project.id)  # ty: ignore[invalid-assignment]
        self._geometry_graph = RenderScene()
        self._toolpath_graph = RenderScene()
        if self._scene is not None:
            self._rebuild_geometry_graph()
        self._rebuild_setup_graph()
        self._rebuild_solid_graph()
        self.project_opened.emit()
        self.operations_changed.emit()
        self.toolpath_changed.emit()

    def _reload_project(self) -> None:
        if self._project is None:
            return
        self._project = self._app.project_service.get_project(self._project.id)
        self._machine = self._app.catalog_repository.machine(self._project.machine_id)
        # Keep scenes in sync after undo/redo
        svc_geo = self._app.project_service.get_geometry_scene(self._project.id)
        self._scene = svc_geo  # type: ignore[assignment]
        svc_scene = self._app.project_service.get_solid_scene(self._project.id)
        self._solid_scene = svc_scene  # ty: ignore[invalid-assignment]
        if self._solid_scene is None:
            self._selected_solid_features.clear()
        self._rebuild_geometry_graph()
        self._rebuild_setup_graph()
        self._rebuild_solid_graph()
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
        offset_x = wcs.offset_x_mm
        offset_y = wcs.offset_y_mm

        # Compute stock center XY respecting Stock.origin
        if stock.origin.value in ("center_xy_top_z", "center_xy_zero_z"):
            # Position is center XY
            target_x = stock.position_x_mm + offset_x
            target_y = stock.position_y_mm + offset_y
        else:
            # Position is corner XY
            target_x = stock.position_x_mm + offset_x + stock.width_mm / 2.0
            target_y = stock.position_y_mm + offset_y + stock.length_mm / 2.0

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
