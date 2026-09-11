"""Tests for the setup frame: stock position, WCS origin and absolute Z."""

from __future__ import annotations

import pytest

from antcam_rc2.core.errors import ToolpathError
from antcam_rc2.core.project.models import Stock, StockOrigin, WorkCoordinateSystem
from antcam_rc2.core.toolpath.setup_frame import (
    calculate_stock_origin_position,
    clearance_z_mm,
    stock_bottom_z_mm,
    stock_center_xy_mm,
    stock_corner_xy_mm,
    stock_top_z_mm,
    target_z_mm,
)


def make_stock(**overrides: object) -> Stock:
    values: dict[str, object] = {
        "width_mm": 30.0,
        "length_mm": 20.0,
        "height_mm": 5.0,
        "position_x_mm": 10.0,
        "position_y_mm": 20.0,
        "position_z_mm": 2.0,
        "material_id": "aluminum_6061",
    }
    values.update(overrides)
    return Stock.model_validate(values)


def make_wcs(**overrides: object) -> WorkCoordinateSystem:
    values: dict[str, object] = {"offset_x_mm": 1.0, "offset_y_mm": 2.0, "offset_z_mm": 3.0}
    values.update(overrides)
    return WorkCoordinateSystem.model_validate(values)


def test_stock_top_and_bottom_include_wcs_offset() -> None:
    stock = make_stock()
    wcs = make_wcs()
    # Default origin is CENTER_XY_TOP_Z: position_z is top Z
    # bottom = top - height = (2+3) - 5 = 0
    # top = position_z + offset_z = 2 + 3 = 5
    assert stock_bottom_z_mm(stock, wcs) == pytest.approx(0.0)
    assert stock_top_z_mm(stock, wcs) == pytest.approx(5.0)


def test_stock_center_and_corner_include_offsets() -> None:
    stock = make_stock()
    wcs = make_wcs()
    # Default origin CENTER_XY_TOP_Z: position is center XY
    # corner = center - width/2, length/2 = (10+1-15, 20+2-10) = (-4, 12)
    # center = position + offset = (11, 22)
    assert stock_corner_xy_mm(stock, wcs) == (-4.0, 12.0)
    assert stock_center_xy_mm(stock, wcs) == (11.0, 22.0)


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        # CENTER_XY_TOP_Z: position = center XY, top Z
        # origin_point = (center_x, center_y, top_z) = (11, 22, 5)
        (StockOrigin.CENTER_XY_TOP_Z, (11.0, 22.0, 5.0)),
        # CORNER_XY_TOP_Z: position = corner XY, top Z
        # origin_point = (corner_x, corner_y, top_z) = (11, 22, 5)
        (StockOrigin.CORNER_XY_TOP_Z, (11.0, 22.0, 5.0)),
        # CENTER_XY_ZERO_Z: position = center XY, zero Z
        # origin_point = (center_x, center_y, 0) = (11, 22, 0)
        (StockOrigin.CENTER_XY_ZERO_Z, (11.0, 22.0, 0.0)),
        # CORNER_XY_ZERO_Z: position = corner XY, zero Z
        # origin_point = (corner_x, corner_y, 0) = (11, 22, 0)
        (StockOrigin.CORNER_XY_ZERO_Z, (11.0, 22.0, 0.0)),
    ],
)
def test_origin_position_resolves_each_stock_origin(origin: StockOrigin, expected) -> None:
    assert calculate_stock_origin_position(make_stock(origin=origin), make_wcs()) == pytest.approx(expected)


def test_target_z_measures_depth_from_stock_top() -> None:
    stock = make_stock()
    wcs = make_wcs()
    # top_z = 5.0, depth=2 -> target = 5 - 2 = 3
    assert target_z_mm(stock, wcs, depth_mm=2.0) == pytest.approx(3.0)
    assert target_z_mm(stock, wcs, depth_mm=2.0, stock_allowance_mm=0.5) == pytest.approx(2.5)
    assert target_z_mm(stock, wcs, depth_mm=None) == pytest.approx(5.0)


def test_clearance_must_be_positive_above_top() -> None:
    stock = make_stock()
    wcs = make_wcs()
    # top_z = 5.0, clearance 5.0 -> 10.0
    assert clearance_z_mm(stock, wcs, clearance_above_stock_mm=5.0) == pytest.approx(10.0)
    with pytest.raises(ToolpathError, match="positive"):
        clearance_z_mm(stock, wcs, clearance_above_stock_mm=0.0)
