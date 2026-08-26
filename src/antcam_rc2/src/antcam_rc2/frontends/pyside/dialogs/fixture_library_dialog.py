"""Dialog for selecting a fixture from the user library."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from antcam_rc2.core.project.fixture_library import FixtureLibrary
from antcam_rc2.core.project.models import Fixture


class FixtureLibraryDialog(QDialog):
    """Dialog to select a fixture from the user library."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Load Fixture from Library")
        self.resize(500, 400)

        layout = QVBoxLayout(self)

        self._list = QListWidget(self)
        layout.addWidget(self._list)

        # Action buttons
        btn_layout = QHBoxLayout()
        delete_btn = QPushButton("Delete", self)
        delete_btn.setObjectName("danger")
        delete_btn.clicked.connect(self._on_delete)
        btn_layout.addStretch()
        btn_layout.addWidget(delete_btn)
        layout.addLayout(btn_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_fixtures()

    def _load_fixtures(self) -> None:
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

    def _on_delete(self) -> None:
        """Delete the selected fixture from the library."""
        item = self._list.currentItem()
        if item is None:
            return
        fixture_id = item.data(0x0100)  # Qt.UserRole
        fixture_name = self._list.currentItem().text().split(" (")[0]

        reply = QMessageBox.question(
            self,
            "Delete Fixture",
            f"Delete fixture '{fixture_name}' from the library?\nThis action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            lib = FixtureLibrary.get_default()
            lib.delete_fixture(fixture_id)
            self._load_fixtures()

    def selected_fixture_id(self) -> str | None:
        """Return the ID of the selected fixture, or None."""
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(0x0100)  # Qt.UserRole
