"""Tests for the immutable Phase 3 project domain models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from pydantic import ValidationError

from antcam_rc2.core.project.models import (
    CatalogSnapshot,
    Fixture,
    FixtureKind,
    GeometryRef,
    Operation,
    OperationParameters,
    OperationType,
    Project,
    Stock,
    StockOrigin,
    WorkCoordinateSystem,
)
from antcam_rc2.core.units import UnitSystem


def make_stock(**overrides: object) -> Stock:
    values: dict[str, object] = {
        "width_mm": 100.0,
        "length_mm": 80.0,
        "height_mm": 12.0,
        "material_id": "aluminum_6061",
    }
    values.update(overrides)
    return Stock.model_validate(values)


def make_operation(**overrides: object) -> Operation:
    values: dict[str, object] = {
        "id": "op_1234abcd",
        "name": "Profile outer contour",
        "operation_type": OperationType.PROFILING,
        "tool_id": "end_mill_3_175_2f",
        "cooling_id": "aerodust",
        "parameters": OperationParameters(),
    }
    values.update(overrides)
    return Operation.model_validate(values)


def make_project(**overrides: object) -> Project:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "id": "proj_1234abcd",
        "name": "Fixture plate",
        "created_at": now,
        "modified_at": now,
        "units": UnitSystem.METRIC,
        "wcs": WorkCoordinateSystem(),
        "machine_id": "makera_z1",
        "stock": make_stock(),
    }
    values.update(overrides)
    return Project.model_validate(values)


def test_project_round_trips_json_as_an_immutable_setup_snapshot() -> None:
    project = make_project(
        fixtures=(
            Fixture(
                id="fix_1234abcd",
                name="Low profile clamp",
                kind=FixtureKind.VISE,
                width_mm=20.0,
                length_mm=40.0,
                height_mm=15.0,
            ),
        ),
        operations=(make_operation(),),
    )

    restored = Project.model_validate_json(project.model_dump_json())

    assert restored == project
    with pytest.raises(ValidationError):
        cast(Any, project).name = "different"


def test_stock_and_operation_parameters_validate_physical_and_catalog_values() -> None:
    assert make_stock().origin is StockOrigin.CENTER_XY_TOP_Z
    assert OperationParameters(stock_allowance_mm=0.2, finishing_passes=1).stock_allowance_mm == 0.2

    with pytest.raises(ValidationError):
        make_stock(width_mm=0.0)
    with pytest.raises(ValidationError):
        make_operation(tool_id="EndMill")
    with pytest.raises(ValidationError):
        OperationParameters(stock_allowance_mm=-0.1)


def test_project_rejects_duplicate_fixture_or_operation_ids() -> None:
    fixture = Fixture(
        id="fix_1234abcd",
        name="Clamp",
        kind=FixtureKind.FIXED,
        width_mm=20.0,
        length_mm=20.0,
        height_mm=10.0,
    )
    with pytest.raises(ValidationError, match="duplicate fixture"):
        make_project(fixtures=(fixture, fixture))

    operation = make_operation()
    with pytest.raises(ValidationError, match="duplicate operation"):
        make_project(operations=(operation, operation))


def test_operation_models_the_full_planned_vocabulary_without_a_strategy_registry() -> None:
    assert len(OperationType) == 18
    operation = make_operation(
        geometry_refs=(
            GeometryRef(
                layer_name="profile",
                entity_index=0,
                entity_type="Contour",
                entity_fingerprint="sha256:" + "a" * 64,
            ),
        )
    )

    assert operation.operation_type is OperationType.PROFILING
    assert operation.geometry_refs[0].entity_index == 0


def test_catalog_snapshot_requires_all_phase_two_catalog_versions() -> None:
    snapshot = CatalogSnapshot(versions={"machines": "1.0", "tools": "1.0", "materials": "1.0", "cooling": "1.0"})
    assert snapshot.versions["tools"] == "1.0"

    with pytest.raises(ValidationError, match="catalog versions"):
        CatalogSnapshot(versions={"machines": "1.0"})
