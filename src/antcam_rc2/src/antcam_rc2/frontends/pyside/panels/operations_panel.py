"""Operations panel: ordered list with every operation action."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListView,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from antcam_rc2.frontends.pyside.controllers.project_controller import ProjectController
from antcam_rc2.frontends.pyside.dialogs.add_operation_dialog import AddOperationDialog
from antcam_rc2.frontends.pyside.settings.clipboard import operation_clipboard
from antcam_rc2.frontends.pyside.widgets.operation_list_model import OperationListModel


class OperationsPanel(QWidget):
    """Ordered operation list with add/duplicate/remove/toggle/reorder/paste."""

    def __init__(self, controller: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._model = OperationListModel(self)
        self._list = QListView(self)
        self._list.setModel(self._model)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self._list.selectionModel().currentChanged.connect(self._on_selection_changed)

        layout = QVBoxLayout(self)
        layout.addWidget(self._list)
        buttons = QHBoxLayout()
        self._add_button = QPushButton("Add", self)
        self._add_button.setObjectName("primary")
        self._up_button = QPushButton("Up", self)
        self._down_button = QPushButton("Down", self)
        self._paste_button = QPushButton("Paste", self)
        buttons.addWidget(self._add_button)
        buttons.addWidget(self._up_button)
        buttons.addWidget(self._down_button)
        buttons.addWidget(self._paste_button)
        layout.addLayout(buttons)

        self._add_button.clicked.connect(self._on_add)
        self._up_button.clicked.connect(lambda: self._move_selected(-1))
        self._down_button.clicked.connect(lambda: self._move_selected(1))
        self._paste_button.clicked.connect(self._on_paste)

    # ------------------------------------------------------------------ refresh
    def refresh(self) -> None:
        project = self._controller.project
        operations = tuple(project.operations) if project is not None else ()
        self._model.set_operations(operations)
        selected = self._controller.selected_operation_id
        if selected is not None:
            row = self._model.row_of(selected)
            if row >= 0:
                self._list.setCurrentIndex(self._model.index(row))

    def selected_operation_id(self) -> str | None:
        index = self._list.currentIndex()
        operation = self._model.operation_at(index.row()) if index.isValid() else None
        return operation.id if operation is not None else None

    # ------------------------------------------------------------------ actions
    def _on_add(self) -> None:
        if self._controller.project is None:
            return
        dialog = AddOperationDialog(self._controller.services, self)
        if dialog.exec():
            operation_id = self._controller.add_operation(
                dialog.operation_type(),
                tool_id=dialog.tool_id(),
                cooling_id=dialog.cooling_id(),
                name=dialog.name(),
            )
            if operation_id is not None:
                self._controller.select_operation(operation_id)

    def _on_selection_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        operation = self._model.operation_at(current.row()) if current.isValid() else None
        self._controller.select_operation(operation.id if operation is not None else None)

    def _move_selected(self, delta: int) -> None:
        operation_id = self.selected_operation_id()
        if operation_id is not None:
            self._controller.move_operation_by(operation_id, delta)

    def _on_paste(self) -> None:
        if self._controller.project is None:
            return
        payload = operation_clipboard.paste()
        if payload is None:
            return
        self._controller.add_operation(
            payload["operation_type"],
            tool_id=payload["tool_id"],
            cooling_id=payload["cooling_id"],
            name=payload.get("name"),
        )

    def _on_context_menu(self, position) -> None:
        operation_id = self.selected_operation_id()
        menu = QMenu(self)
        add_action = menu.addAction("Add operation...")
        menu.addSeparator()
        duplicate_action = menu.addAction("Duplicate")
        copy_action = menu.addAction("Copy")
        remove_action = menu.addAction("Remove")
        menu.addSeparator()
        toggle_action = menu.addAction("Toggle enabled")
        up_action = menu.addAction("Move up")
        down_action = menu.addAction("Move down")
        action = menu.exec(self._list.mapToGlobal(position))
        if action is None:
            return
        if action is add_action:
            self._on_add()
        elif operation_id is None:
            return
        elif action is duplicate_action:
            self._controller.duplicate_operation(operation_id)
        elif action is copy_action:
            operation = next(
                (
                    candidate
                    for candidate in (self._controller.project.operations if self._controller.project else ())
                    if candidate.id == operation_id
                ),
                None,
            )
            if operation is not None:
                operation_clipboard.copy(operation)
        elif action is remove_action:
            self._controller.remove_operation(operation_id)
        elif action is toggle_action:
            operation = next(
                (
                    candidate
                    for candidate in (self._controller.project.operations if self._controller.project else ())
                    if candidate.id == operation_id
                ),
                None,
            )
            if operation is not None:
                self._controller.toggle_operation(operation_id, not operation.enabled)
        elif action is up_action:
            self._move_selected(-1)
        elif action is down_action:
            self._move_selected(1)
