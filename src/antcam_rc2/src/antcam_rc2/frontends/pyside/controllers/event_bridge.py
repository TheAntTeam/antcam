"""Bridges core domain events to Qt signals on the main thread.

The kernel emits events synchronously on its own event bus; the bridge
subscribes on the main thread and re-emits them as typed Qt signals so panels
and the viewport can react without polling.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from antcam_rc2.core.services.events import (
    Event,
    FixtureAdded,
    FixtureChanged,
    FixtureRemoved,
    MachineChanged,
    OperationAdded,
    OperationRemoved,
    OperationReordered,
    OperationToggled,
    ProjectCreated,
    ProjectLoaded,
    StockChanged,
)


class EventBridge(QObject):
    """Translates core domain events into Qt signals."""

    project_created = Signal(str)
    project_loaded = Signal(str, str)
    operation_added = Signal(str, str)
    operation_removed = Signal(str, str)
    operation_reordered = Signal(str, str, int)
    operation_toggled = Signal(str, str, bool)
    stock_changed = Signal(str)
    fixture_added = Signal(str, str)
    fixture_removed = Signal(str, str)
    fixture_changed = Signal(str, str)
    machine_changed = Signal(str, str)

    def __init__(self, event_bus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._event_bus = event_bus
        event_bus.subscribe(Event, self._on_event)

    def _on_event(self, event: Event) -> None:
        """Route one core event to the matching Qt signal."""
        if isinstance(event, ProjectCreated):
            self.project_created.emit(event.project_id)
        elif isinstance(event, ProjectLoaded):
            self.project_loaded.emit(event.project_id, event.name)
        elif isinstance(event, OperationAdded):
            self.operation_added.emit(event.project_id, event.operation_id)
        elif isinstance(event, OperationRemoved):
            self.operation_removed.emit(event.project_id, event.operation_id)
        elif isinstance(event, OperationReordered):
            self.operation_reordered.emit(event.project_id, event.operation_id, event.new_index)
        elif isinstance(event, OperationToggled):
            self.operation_toggled.emit(event.project_id, event.operation_id, event.enabled)
        elif isinstance(event, StockChanged):
            self.stock_changed.emit(event.project_id)
        elif isinstance(event, FixtureAdded):
            self.fixture_added.emit(event.project_id, event.fixture_id)
        elif isinstance(event, FixtureRemoved):
            self.fixture_removed.emit(event.project_id, event.fixture_id)
        elif isinstance(event, FixtureChanged):
            self.fixture_changed.emit(event.project_id, event.fixture_id)
        elif isinstance(event, MachineChanged):
            self.machine_changed.emit(event.project_id, event.machine_id)

    def dispose(self) -> None:
        """Unsubscribe from the event bus (idempotent)."""
        if self._event_bus is not None:
            self._event_bus.unsubscribe(Event, self._on_event)
            self._event_bus = None  # type: ignore[assignment]
