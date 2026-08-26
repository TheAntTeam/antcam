"""Tests for app.container."""

from __future__ import annotations

import pytest

from antcam_rc2.app.container import Container
from antcam_rc2.core.errors import ConfigurationError


class _Service:
    def __init__(self) -> None:
        self.calls = 0

    def ping(self) -> int:
        self.calls += 1
        return self.calls


def test_resolve_runs_factory() -> None:
    container = Container()
    container.register("svc", _Service)
    assert isinstance(container.resolve("svc"), _Service)


def test_singleton_cached() -> None:
    container = Container()
    container.register("svc", _Service, singleton=True)
    first = container.resolve("svc")
    second = container.resolve("svc")
    assert first is second


def test_non_singleton_fresh_each_time() -> None:
    container = Container()
    container.register("svc", _Service, singleton=False)
    assert container.resolve("svc") is not container.resolve("svc")


def test_lazy_factory_not_called_on_register() -> None:
    container = Container()
    called = False

    def factory() -> _Service:
        nonlocal called
        called = True
        return _Service()

    container.register("svc", factory)
    assert not called
    container.resolve("svc")
    assert called


def test_unknown_key_raises() -> None:
    container = Container()
    with pytest.raises(ConfigurationError):
        container.resolve("missing")


def test_override_and_reset() -> None:
    container = Container()
    container.register("svc", _Service)
    stub = _Service()
    container.override("svc", stub)
    assert container.resolve("svc") is stub
    container.reset_override("svc")
    assert container.resolve("svc") is not stub


def test_empty_key_raises() -> None:
    container = Container()
    with pytest.raises(ConfigurationError):
        container.register("", _Service)


def test_has() -> None:
    container = Container()
    container.register("svc", _Service)
    assert container.has("svc")
    assert not container.has("other")
