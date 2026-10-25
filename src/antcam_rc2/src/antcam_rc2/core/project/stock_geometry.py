"""Stock geometry helpers: single source for stock min/max corners and fixture anchoring.

All functions are pure (no Qt, no I/O) and WCS+StockOrigin-aware.
"""

from __future__ import annotations

from antcam_rc2.core.project.models import Stock, WorkCoordinateSystem


def stock_min_corner(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> tuple[float, float, float]:
    """Return the stock minimum corner (min_x, min_y, min_z) in WCS coordinates.

    This is the anchor point for fixture offsets:
    - min_x = left
    - min_y = front
    - min_z = bottom

    Respects ``Stock.origin`` and ``WorkCoordinateSystem`` offset.
    """
    ox = 0.0 if wcs is None else float(wcs.offset_x_mm)
    oy = 0.0 if wcs is None else float(wcs.offset_y_mm)
    oz = 0.0 if wcs is None else float(wcs.offset_z_mm)
    px = float(stock.position_x_mm) + ox
    py = float(stock.position_y_mm) + oy
    pz = float(stock.position_z_mm) + oz

    if stock.origin.value in ("center_xy_top_z", "center_xy_zero_z"):
        min_x = px - float(stock.width_mm) / 2.0
        min_y = py - float(stock.length_mm) / 2.0
    else:  # corner_xy_*
        min_x = px
        min_y = py

    if stock.origin.value in ("center_xy_top_z", "corner_xy_top_z"):
        min_z = pz - float(stock.height_mm)
    else:  # zero_z
        min_z = pz

    return (min_x, min_y, min_z)


def stock_max_corner(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> tuple[float, float, float]:
    """Return the stock maximum corner (max_x, max_y, max_z) in WCS coordinates."""
    min_x, min_y, min_z = stock_min_corner(stock, wcs)
    return (
        min_x + float(stock.width_mm),
        min_y + float(stock.length_mm),
        min_z + float(stock.height_mm),
    )


def stock_center_xy(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> tuple[float, float]:
    """Return the stock center XY in WCS coordinates."""
    min_x, min_y, _ = stock_min_corner(stock, wcs)
    return (min_x + float(stock.width_mm) / 2.0, min_y + float(stock.length_mm) / 2.0)


def stock_top_z(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> float:
    """Return the stock top Z in WCS coordinates."""
    _, _, max_z = stock_max_corner(stock, wcs)
    return max_z


def stock_bottom_z(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> float:
    """Return the stock bottom Z in WCS coordinates."""
    _, _, min_z = stock_min_corner(stock, wcs)
    return min_z


def fixture_absolute_position(
    fixture,  # Fixture (forward ref to avoid circular import)
    stock: Stock,
    wcs: WorkCoordinateSystem | None = None,
) -> tuple[float, float, float]:
    """Return the absolute WCS position of a fixture from its stock-relative offset."""
    min_x, min_y, min_z = stock_min_corner(stock, wcs)
    return (
        min_x + float(fixture.position_x_mm),
        min_y + float(fixture.position_y_mm),
        min_z + float(fixture.position_z_mm),
    )


def fixture_offset_from_absolute(
    absolute_pos: tuple[float, float, float],
    stock: Stock,
    wcs: WorkCoordinateSystem | None = None,
) -> tuple[float, float, float]:
    """Convert an absolute WCS position to a stock-relative fixture offset."""
    min_x, min_y, min_z = stock_min_corner(stock, wcs)
    return (
        absolute_pos[0] - min_x,
        absolute_pos[1] - min_y,
        absolute_pos[2] - min_z,
    )


__all__ = [
    "stock_min_corner",
    "stock_max_corner",
    "stock_center_xy",
    "stock_top_z",
    "stock_bottom_z",
    "fixture_absolute_position",
    "fixture_offset_from_absolute",
]