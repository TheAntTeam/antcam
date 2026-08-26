"""Synchronous, typed in-process event bus."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from antcam_rc2.core.errors import AntcamError
from antcam_rc2.core.services.events import Event

TEvent = TypeVar("TEvent", bound=Event)

EventHandler = Callable[[TEvent], None]


class EventBusError(AntcamError):
    """Raised when a handler fails during event dispatch."""

    code = "event_bus_error"


class EventBus:
    """Routes events to subscribed handlers synchronously.

    Handlers are filtered by the event type they subscribe to; subclasses are
    matched as well (``subscribe(Event, ...)`` receives every event).

    Exception policy: :class:`AntcamError` raised by a handler propagates
    unchanged (domain errors from services must not be masked); any other
    exception is wrapped in :class:`EventBusError`.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[EventHandler[Any]]] = defaultdict(list)

    def subscribe(self, event_type: type[TEvent], handler: EventHandler[TEvent]) -> None:
        """Register ``handler`` to be called for events of ``event_type``."""
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type[TEvent], handler: EventHandler[TEvent]) -> None:
        """Remove a previously registered handler for ``event_type``."""
        handlers = self._handlers.get(event_type)
        if handlers is None:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            pass

    def emit(self, event: Event) -> None:
        """Dispatch ``event`` to every matching subscribed handler."""
        for event_type, handlers in list(self._handlers.items()):
            if not isinstance(event, event_type):
                continue
            for handler in handlers:
                try:
                    handler(event)
                except AntcamError:
                    raise
                except Exception as exc:  # noqa: BLE001 - re-raised as bus error
                    raise EventBusError(f"Handler {handler!r} failed for {type(event).__name__}") from exc

    def clear(self) -> None:
        """Remove all subscriptions."""
        self._handlers.clear()

    def subscriptions(self) -> Iterable[tuple[type[Event], int]]:
        """Yield ``(event_type, handler_count)`` for all subscribed types."""
        return ((event_type, len(handlers)) for event_type, handlers in self._handlers.items())
