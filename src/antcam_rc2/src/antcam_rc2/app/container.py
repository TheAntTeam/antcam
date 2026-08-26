"""Lightweight dependency injection container with lazy resolution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from antcam_rc2.core.errors import ConfigurationError

T = TypeVar("T")
Factory = Callable[[], T]


class _Registration[T]:
    __slots__ = ("factory", "singleton", "_instance")

    def __init__(self, factory: Factory[T], singleton: bool) -> None:
        self.factory = factory
        self.singleton = singleton
        self._instance: T | None = None

    def resolve(self) -> T:
        if self.singleton:
            if self._instance is None:
                self._instance = self.factory()
            if self._instance is None:
                raise RuntimeError(f"Factory for {type(self).__name__} returned None")
            return self._instance
        return self.factory()


class Container:
    """A minimal DI container.

    Services are registered by a string key and resolved lazily. Singleton
    services cache their instance; factories can be overridden with concrete
    instances for testing via :meth:`override`.
    """

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration[Any]] = {}
        self._overrides: dict[str, Any] = {}

    def register(self, key: str, factory: Factory[Any], *, singleton: bool = True) -> None:
        """Register a factory for ``key``."""
        if not key:
            raise ConfigurationError("Service key must not be empty")
        self._registrations[key] = _Registration(factory, singleton=singleton)

    def resolve(self, key: str) -> Any:
        """Resolve the service registered under ``key`` (lazily)."""
        if key in self._overrides:
            return self._overrides[key]
        registration = self._registrations.get(key)
        if registration is None:
            raise ConfigurationError(f"Unknown service: {key!r}")
        return registration.resolve()

    def override(self, key: str, instance: Any) -> None:
        """Override a service with a concrete instance (used in tests)."""
        self._overrides[key] = instance

    def reset_override(self, key: str) -> None:
        """Remove a previously registered override."""
        self._overrides.pop(key, None)

    def has(self, key: str) -> bool:
        """Return whether ``key`` is registered or overridden."""
        return key in self._registrations or key in self._overrides
