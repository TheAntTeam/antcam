"""Tests for app.event_bus."""

from __future__ import annotations

import pytest

from antcam_rc2.app.event_bus import EventBus, EventBusError
from antcam_rc2.app.events import Event, LogEvent, ProjectCreated


def test_subscribe_and_emit() -> None:
    bus = EventBus()
    received: list[Event] = []

    def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe(ProjectCreated, handler)
    event = ProjectCreated(project_id="p_1")
    bus.emit(event)
    assert received == [event]


def test_subclass_matches_base_subscription() -> None:
    bus = EventBus()
    received: list[Event] = []

    bus.subscribe(Event, received.append)
    bus.emit(LogEvent(level="INFO", message="hi", source="test"))
    assert len(received) == 1


def test_unsubscribe() -> None:
    bus = EventBus()
    received: list[Event] = []

    def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe(ProjectCreated, handler)
    bus.unsubscribe(ProjectCreated, handler)
    bus.emit(ProjectCreated(project_id="p_1"))
    assert received == []


def test_unsubscribe_missing_is_noop() -> None:
    bus = EventBus()

    def handler(event: Event) -> None:
        pass

    bus.unsubscribe(ProjectCreated, handler)  # never subscribed


def test_duplicate_subscription_ignored() -> None:
    bus = EventBus()
    received: list[Event] = []

    def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe(ProjectCreated, handler)
    bus.subscribe(ProjectCreated, handler)
    bus.emit(ProjectCreated(project_id="p_1"))
    assert len(received) == 1


def test_handler_error_wrapped() -> None:
    bus = EventBus()

    def handler(event: Event) -> None:
        raise RuntimeError("nope")

    bus.subscribe(ProjectCreated, handler)
    with pytest.raises(EventBusError):
        bus.emit(ProjectCreated(project_id="p_1"))


def test_domain_error_propagates_unwrapped() -> None:
    from antcam_rc2.core.errors import ToolpathError

    bus = EventBus()

    def handler(event: Event) -> None:
        raise ToolpathError("domain failure")

    bus.subscribe(ProjectCreated, handler)
    with pytest.raises(ToolpathError, match="domain failure"):
        bus.emit(ProjectCreated(project_id="p_1"))


def test_clear() -> None:
    bus = EventBus()
    received: list[Event] = []

    bus.subscribe(ProjectCreated, received.append)
    bus.clear()
    bus.emit(ProjectCreated(project_id="p_1"))
    assert received == []


def test_subscriptions_report_counts() -> None:
    bus = EventBus()

    def handler(event: Event) -> None:
        pass

    bus.subscribe(ProjectCreated, handler)
    bus.subscribe(ProjectCreated, handler)
    counts = dict(bus.subscriptions())
    assert counts[ProjectCreated] == 1
