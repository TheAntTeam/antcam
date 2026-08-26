"""Tests for core.errors."""

from __future__ import annotations

import pytest

from antcam_rc2.core.errors import (
    AntcamError,
    ConfigurationError,
    GeometryError,
    OperationError,
    PostProcessorError,
    SimulationError,
    ToolpathError,
    UnsupportedFormatError,
)


def test_error_carries_message() -> None:
    error = AntcamError("boom")
    assert error.message == "boom"
    assert str(error) == "boom"


@pytest.mark.parametrize(
    ("error_type", "expected_code"),
    [
        (AntcamError, "antcam_error"),
        (ConfigurationError, "configuration_error"),
        (UnsupportedFormatError, "unsupported_format"),
        (GeometryError, "geometry_error"),
        (OperationError, "operation_error"),
        (ToolpathError, "toolpath_error"),
        (SimulationError, "simulation_error"),
        (PostProcessorError, "post_processor_error"),
    ],
)
def test_error_codes(error_type: type[AntcamError], expected_code: str) -> None:
    assert error_type("x").code == expected_code


def test_catch_by_base() -> None:
    with pytest.raises(AntcamError):
        raise ToolpathError("nope")


def test_geometry_error_is_antcam_error() -> None:
    assert issubclass(GeometryError, AntcamError)
