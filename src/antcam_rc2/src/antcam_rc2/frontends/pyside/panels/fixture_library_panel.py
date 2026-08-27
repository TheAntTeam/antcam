"""Fixture library panel: manage saved fixtures."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from antcam_rc2.core.project.fixture_library import FixtureLibrary
from antcam_rc2.core.project.models import Fixture
from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController


class FixtureLibraryPanel(QWidget):
    """Panel for managing the user fixture library."""

    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

        layout = QVBoxLayout(self)

        self._list = QListWidget(self)
        layout.addWidget(self._list)

        btn_layout = QVBoxLayout()
        add_to_project = QPushButton("Add to Project", self)
        delete_fixture = QPushButton("Delete", self)
        delete_fixture.setObjectName("danger")
        refresh = QPushButton("Refresh", self)
        btn_layout.addWidget(add_to_project)
        btn_layout.addWidget(delete_fixture)
        btn_layout.addWidget(refresh)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        add_to_project.clicked.connect(self._on_add_to_project)
        delete_fixture.clicked.connect(self._on_delete)
        refresh.clicked.connect(self.refresh)
        self._list.itemDoubleClicked.connect(self._on_add_to_project)

        # Refresh when library changes
        controller.fixture_library_changed.connect(self.refresh)

        # Connect to geometry import controller busy state
        self._controller.geometry_import_controller.busy_changed.connect(self._on_import_busy)
        self._add_button = add_to_project

    def refresh(self) -> None:
        """Reload the fixture list from the library."""
        lib = FixtureLibrary.get_default()
        fixtures = lib.list_fixtures()
        self._list.clear()
        for fixture in fixtures:
            item = QListWidgetItem(self._format_fixture(fixture))
            item.setData(0x0100, fixture.id)  # Qt.UserRole
            self._list.addItem(item)

    @staticmethod
    def _format_fixture(fixture: Fixture) -> str:
        if fixture.kind.value == "screw":
            screw_info = ""
            if fixture.screw_diameter_mm:
                screw_info += f" ⌀{fixture.screw_diameter_mm:g}mm"
            if fixture.screw_length_mm:
                screw_info += f" L{fixture.screw_length_mm:g}mm"
            if fixture.hole_diameter_mm:
                screw_info += f" hole⌀{fixture.hole_diameter_mm:g}mm"
            return f"{fixture.name} ({fixture.kind.value}){screw_info}"
        mesh_info = " 📦" if fixture.mesh_path else ""
        return (
            f"{fixture.name} ({fixture.kind.value}) "
            f"{fixture.width_mm:g}×{fixture.length_mm:g}×{fixture.height_mm:g} mm{mesh_info}"
        )

    def _on_add_to_project(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        fixture_id = item.data(0x0100)  # Qt.UserRole
        if fixture_id:
            self._controller.load_fixture_from_library(fixture_id)

    def _on_delete(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        fixture_id = item.data(0x0100)  # Qt.UserRole
        if fixture_id:
            lib = FixtureLibrary.get_default()
            lib.delete_fixture(fixture_id)
            self.refresh()

    def _on_import_busy(self, busy: bool) -> None:
        """Update button states when fixture import is in progress."""
        self._add_button.setEnabled(not busy)
        if busy:
            self._add_button.setText("Loading...")
        else:
            self._add_button.setText("Add to Project")
