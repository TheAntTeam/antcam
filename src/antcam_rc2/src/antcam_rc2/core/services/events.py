"""UI-agnostic domain event contracts emitted by application services."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Event(BaseModel):
    """Base class for immutable, JSON-serializable domain events."""

    model_config = ConfigDict(frozen=True)


class ProjectCreated(Event):
    """A new project was created."""

    project_id: str


class ProjectLoaded(Event):
    """A persisted project document was loaded successfully."""

    project_id: str
    name: str


class OperationAdded(Event):
    """An operation was added to a project."""

    project_id: str
    operation_id: str
    operation_type: str


class OperationRemoved(Event):
    """An operation was removed from a project."""

    project_id: str
    operation_id: str


class OperationReordered(Event):
    """An operation changed its position in the project list."""

    project_id: str
    operation_id: str
    new_index: int


class OperationToggled(Event):
    """An operation was enabled or disabled."""

    project_id: str
    operation_id: str
    enabled: bool


class StockChanged(Event):
    """The project stock setup changed."""

    project_id: str


class FixtureAdded(Event):
    """A fixture was added to the project setup."""

    project_id: str
    fixture_id: str


class FixtureRemoved(Event):
    """A fixture was removed from the project setup."""

    project_id: str
    fixture_id: str


class FixtureChanged(Event):
    """A fixture's properties were edited."""

    project_id: str
    fixture_id: str


class MachineChanged(Event):
    """The selected machine profile changed."""

    project_id: str
    machine_id: str


class ToolpathGenerated(Event):
    """A toolpath was generated for an operation."""

    project_id: str
    operation_id: str


class LogEvent(Event):
    """A structured log record produced by the kernel."""

    level: str
    message: str
    source: str
