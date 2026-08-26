"""Setup frame calculation: stock position, WCS origin, and absolute Z coordinates."""

from __future__ import annotations

from antcam_rc2.core.errors import ToolpathError
from antcam_rc2.core.project.models import Stock, StockOrigin, WorkCoordinateSystem


def stock_top_z_mm(stock: Stock, wcs: WorkCoordinateSystem) -> float:
    """Return the absolute Z of the stock top face in WCS coordinates."""
    return stock.position_z_mm + wcs.offset_z_mm + stock.height_mm


def stock_bottom_z_mm(stock: Stock, wcs: WorkCoordinateSystem) -> float:
    """Return the absolute Z of the stock bottom face in WCS coordinates."""
    return stock.position_z_mm + wcs.offset_z_mm


def stock_center_xy_mm(stock: Stock, wcs: WorkCoordinateSystem) -> tuple[float, float]:
    """Return the absolute XY center of the stock in WCS coordinates."""
    center_x = stock.position_x_mm + wcs.offset_x_mm + stock.width_mm / 2.0
    center_y = stock.position_y_mm + wcs.offset_y_mm + stock.length_mm / 2.0
    return center_x, center_y


def stock_corner_xy_mm(stock: Stock, wcs: WorkCoordinateSystem) -> tuple[float, float]:
    """Return the absolute XY corner (min) of the stock in WCS coordinates."""
    corner_x = stock.position_x_mm + wcs.offset_x_mm
    corner_y = stock.position_y_mm + wcs.offset_y_mm
    return corner_x, corner_y


def calculate_stock_origin_position(stock: Stock, wcs: WorkCoordinateSystem) -> tuple[float, float, float]:
    """Calculate the absolute stock origin position based on StockOrigin."""
    origin = stock.origin
    if origin == StockOrigin.CENTER_XY_TOP_Z:
        cx, cy = stock_center_xy_mm(stock, wcs)
        return (cx, cy, stock_top_z_mm(stock, wcs))
    if origin == StockOrigin.CORNER_XY_TOP_Z:
        cx, cy = stock_corner_xy_mm(stock, wcs)
        return (cx, cy, stock_top_z_mm(stock, wcs))
    if origin == StockOrigin.CENTER_XY_ZERO_Z:
        cx, cy = stock_center_xy_mm(stock, wcs)
        return (cx, cy, 0.0)
    if origin == StockOrigin.CORNER_XY_ZERO_Z:
        cx, cy = stock_corner_xy_mm(stock, wcs)
        return (cx, cy, 0.0)
    raise ToolpathError(f"unknown stock origin: {origin}")


def target_z_mm(
    stock: Stock,
    wcs: WorkCoordinateSystem,
    depth_mm: float | None,
    stock_allowance_mm: float = 0.0,
) -> float:
    """Calculate absolute target Z for an operation.

    Depth is measured from stock top toward -Z. If depth is None, returns stock top.
    """
    top = stock_top_z_mm(stock, wcs)
    if depth_mm is None:
        return top - stock_allowance_mm
    return top - depth_mm - stock_allowance_mm


def clearance_z_mm(
    stock: Stock,
    wcs: WorkCoordinateSystem,
    clearance_above_stock_mm: float,
) -> float:
    """Calculate absolute clearance Z above stock top."""
    if clearance_above_stock_mm <= 0:
        raise ToolpathError("clearance_above_stock_mm must be positive")
    return stock_top_z_mm(stock, wcs) + clearance_above_stock_mm
