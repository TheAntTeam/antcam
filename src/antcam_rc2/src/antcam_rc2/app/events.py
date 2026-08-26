"""Compatibility re-exports for the core domain event contracts."""

from antcam_rc2.core.services.events import (
    Event,
    FixtureAdded,
    FixtureChanged,
    FixtureRemoved,
    LogEvent,
    MachineChanged,
    OperationAdded,
    OperationRemoved,
    OperationReordered,
    OperationToggled,
    ProjectCreated,
    ProjectLoaded,
    StockChanged,
    ToolpathGenerated,
)

__all__ = [
    "Event",
    "FixtureAdded",
    "FixtureChanged",
    "FixtureRemoved",
    "LogEvent",
    "MachineChanged",
    "OperationAdded",
    "OperationRemoved",
    "OperationReordered",
    "OperationToggled",
    "ProjectCreated",
    "ProjectLoaded",
    "StockChanged",
    "ToolpathGenerated",
]
