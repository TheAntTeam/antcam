"""Tests for core.units."""

from __future__ import annotations

import pytest

from antcam_rc2.core.units import MM_PER_INCH, UnitSystem, convert, in_to_mm, mm_to_in


def test_mm_to_in() -> None:
    assert mm_to_in(25.4) == pytest.approx(1.0)


def test_in_to_mm() -> None:
    assert in_to_mm(1.0) == pytest.approx(25.4)


def test_roundtrip() -> None:
    value = 12.7
    assert in_to_mm(mm_to_in(value)) == pytest.approx(value)


def test_convert_same_system() -> None:
    assert convert(5.0, UnitSystem.METRIC, UnitSystem.METRIC) == pytest.approx(5.0)


def test_convert_metric_to_imperial() -> None:
    assert convert(25.4, UnitSystem.METRIC, UnitSystem.IMPERIAL) == pytest.approx(1.0)


def test_convert_imperial_to_metric() -> None:
    assert convert(2.0, UnitSystem.IMPERIAL, UnitSystem.METRIC) == pytest.approx(2.0 * MM_PER_INCH)


def test_convert_unknown_system_raises() -> None:
    with pytest.raises(ValueError):
        convert(1.0, "metric", "inches")
    with pytest.raises(ValueError):
        convert(1.0, "yards", UnitSystem.METRIC)


def test_convert_accepts_string_systems() -> None:
    assert convert(25.4, "metric", "imperial") == pytest.approx(1.0)
