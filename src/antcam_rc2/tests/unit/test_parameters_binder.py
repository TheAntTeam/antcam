"""Tests for the schema-driven parameters binder (offscreen Qt)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_fields_from_schema_maps_pydantic_properties() -> None:
    from antcam_rc2.frontends.pyside.widgets.parameters_binder import fields_from_schema

    schema = {
        "properties": {
            "pitch_mm": {"title": "Pitch (mm)", "type": "number", "exclusiveMinimum": 0},
            "direction": {"title": "Direction", "type": "string", "enum": ["up", "down"], "default": "up"},
            "enabled": {"title": "Enabled", "type": "boolean", "default": True},
        }
    }
    fields = {field.key: field for field in fields_from_schema(schema)}
    assert fields["pitch_mm"].kind == "number"
    assert fields["direction"].kind == "choice"
    assert fields["direction"].choices == ("up", "down")
    assert fields["direction"].default == "up"
    assert fields["enabled"].kind == "boolean"


def test_common_fields_match_operation_defaults() -> None:
    from antcam_rc2.core.project.models import OperationParameters
    from antcam_rc2.frontends.pyside.widgets.parameters_binder import common_parameter_fields

    defaults = OperationParameters().model_dump()
    specs = {field.key: field for field in common_parameter_fields()}
    assert specs["climb_cut"].default is defaults["climb_cut"]
    assert specs["optimize_path_order"].default is defaults["optimize_path_order"]
    assert specs["tolerance_mm"].default == defaults["tolerance_mm"]


def test_binder_round_trips_operation_parameters(qapp) -> None:
    from antcam_rc2.core.feeds_speeds.models import FeedSpeedOverrides
    from antcam_rc2.core.operations.params import ThreadMillingParameters
    from antcam_rc2.core.project.models import OperationParameters
    from antcam_rc2.frontends.pyside.widgets.parameters_binder import ParametersBinder

    binder = ParametersBinder()
    binder.rebuild(ThreadMillingParameters.model_json_schema())

    source = OperationParameters(
        depth_mm=3.0,
        stepdown_mm=1.0,
        tolerance_mm=0.05,
        climb_cut=True,
        strategy_parameters={"pitch_mm": 0.5, "thread_diameter_mm": 4.0, "direction": "down"},
        feed_speed_overrides=FeedSpeedOverrides(rpm=3000.0, cut_feed_mm_min=120.0),
    )
    binder.set_parameters(source, ThreadMillingParameters.model_json_schema())
    result = binder.to_parameters()

    assert result.depth_mm == pytest.approx(3.0)
    assert result.stepdown_mm == pytest.approx(1.0)
    assert result.tolerance_mm == pytest.approx(0.05)
    assert result.climb_cut is True
    assert result.strategy_parameters["pitch_mm"] == pytest.approx(0.5)
    assert result.strategy_parameters["direction"] == "down"
    assert result.feed_speed_overrides.rpm == pytest.approx(3000.0)
    assert result.feed_speed_overrides.cut_feed_mm_min == pytest.approx(120.0)


def test_binder_keeps_overrides_auto_when_unset(qapp) -> None:
    from antcam_rc2.core.project.models import OperationParameters
    from antcam_rc2.frontends.pyside.widgets.parameters_binder import ParametersBinder

    binder = ParametersBinder()
    binder.rebuild(None)
    result = binder.to_parameters()
    assert result.feed_speed_overrides == OperationParameters().feed_speed_overrides
    assert result.strategy_parameters == {}
