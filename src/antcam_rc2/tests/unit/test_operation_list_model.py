"""Tests for the operation list Qt model (offscreen platform)."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import Qt

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def make_operation(operation_id: str, name: str, enabled: bool = True):
    from antcam_rc2.core.project.models import Operation, OperationParameters, OperationType

    return Operation(
        id=operation_id,
        name=name,
        operation_type=OperationType.PROFILING,
        tool_id="end_mill_3_175_2f",
        cooling_id="aerodust",
        enabled=enabled,
        parameters=OperationParameters(),
    )


def test_model_rows_and_roles(qapp) -> None:
    from antcam_rc2.frontends.pyside.widgets.operation_list_model import OperationListModel

    model = OperationListModel()
    operations = (make_operation("op_11111111", "First"), make_operation("op_22222222", "Second", enabled=False))
    model.set_operations(operations)

    assert model.rowCount() == 2
    assert model.data(model.index(0), Qt.ItemDataRole.DisplayRole) == "First"
    assert model.data(model.index(1), Qt.ItemDataRole.UserRole) == "op_22222222"
    assert model.data(model.index(1), Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked
    assert model.data(model.index(0), Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    assert model.data(model.index(0), Qt.ItemDataRole.DecorationRole) == "profiling"


def test_model_lookup_helpers(qapp) -> None:
    from antcam_rc2.frontends.pyside.widgets.operation_list_model import OperationListModel

    model = OperationListModel()
    operations = (make_operation("op_11111111", "First"),)
    model.set_operations(operations)

    assert model.operation_at(0).id == "op_11111111"
    assert model.operation_at(5) is None
    assert model.row_of("op_11111111") == 0
    assert model.row_of("missing") == -1


def test_model_invalid_index_returns_none(qapp) -> None:
    from PySide6.QtCore import QModelIndex

    from antcam_rc2.frontends.pyside.widgets.operation_list_model import OperationListModel

    model = OperationListModel()
    assert model.data(QModelIndex()) is None
    assert model.rowCount(QModelIndex()) == 0
