"""Qt model over the ordered project operations (thin, refresh-on-events)."""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from antcam_rc2.core.project.models import Operation


class OperationListModel(QAbstractListModel):
    """Read-only list model exposing operation rows with role data."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._operations: tuple[Operation, ...] = ()

    def set_operations(self, operations: tuple[Operation, ...]) -> None:
        """Replace the model contents and notify views."""
        self.beginResetModel()
        self._operations = operations
        self.endResetModel()

    def rowCount(self, parent=None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._operations)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # ty: ignore[invalid-method-override]
        """Return the requested role data; PySide6 stubs type ``role`` differently."""
        if not index.isValid() or not (0 <= index.row() < len(self._operations)):
            return None
        operation = self._operations[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return operation.name
        if role == Qt.ItemDataRole.UserRole:
            return operation.id
        if role == Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Checked if operation.enabled else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DecorationRole:
            return operation.operation_type.value
        return None

    def operation_at(self, row: int) -> Operation | None:
        if 0 <= row < len(self._operations):
            return self._operations[row]
        return None

    def row_of(self, operation_id: str) -> int:
        for index, operation in enumerate(self._operations):
            if operation.id == operation_id:
                return index
        return -1
