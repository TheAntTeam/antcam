"""Tests for deterministic Phase 4 operation strategy registration."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from antcam_rc2.core.errors import OperationError
from antcam_rc2.core.operations.contracts import OperationDefinition
from antcam_rc2.core.operations.registry import OperationRegistry, build_standard_registry
from antcam_rc2.core.project.models import OperationType


class EmptyParameters(BaseModel):
    pass


def test_standard_registry_contains_every_persistent_operation_type_in_canonical_order() -> None:
    registry = build_standard_registry()

    assert registry.operation_types() == tuple(OperationType)
    assert len(registry.definitions()) == 18
    assert registry.definition(OperationType.PROFILING).parameters_model is not None


def test_registry_rejects_duplicate_or_unknown_definitions() -> None:
    registry = OperationRegistry()
    definition = OperationDefinition(
        operation_type=OperationType.PROFILING,
        display_name="Profile",
        parameters_model=EmptyParameters,
        strategy_factory=lambda: object(),
    )
    registry.register(definition)

    with pytest.raises(OperationError, match="already registered"):
        registry.register(definition)
    with pytest.raises(OperationError, match="not registered"):
        registry.definition(OperationType.DRILL)
