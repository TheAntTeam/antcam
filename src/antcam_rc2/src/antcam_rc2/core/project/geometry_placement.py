"""Helpers for non-destructive 2D geometry placement (WCS-aware, stock-aware)."""

from __future__ import annotations

import math

from antcam_rc2.core.geometry.transform import Affine2D
from antcam_rc2.core.io.scene import GeometryScene
from antcam_rc2.core.project.models import GeometryPlacement, Stock, WorkCoordinateSystem

# Default "Z offset from top" applied to a freshly imported 2D geometry: a
# positive offset raises the drawing above the stock top by 1 mm so it is
# always visible/clear of the stock surface.
_DEFAULT_Z_OFFSET_MM = 1.0


def stock_center_xy(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> tuple[float, float]:
    ox = 0.0 if wcs is None else float(wcs.offset_x_mm)
    oy = 0.0 if wcs is None else float(wcs.offset_y_mm)
    if stock.origin.value in ("center_xy_top_z", "center_xy_zero_z"):
        return (float(stock.position_x_mm) + ox, float(stock.position_y_mm) + oy)
    return (
        float(stock.position_x_mm) + ox + float(stock.width_mm) / 2.0,
        float(stock.position_y_mm) + oy + float(stock.length_mm) / 2.0,
    )


def stock_top_z(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> float:
    oz = 0.0 if wcs is None else float(wcs.offset_z_mm)
    if stock.origin.value in ("center_xy_top_z", "corner_xy_top_z"):
        return float(stock.position_z_mm) + oz
    return float(stock.position_z_mm) + oz + float(stock.height_mm)


def stock_bottom_z(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> float:
    oz = 0.0 if wcs is None else float(wcs.offset_z_mm)
    if stock.origin.value in ("center_xy_top_z", "corner_xy_top_z"):
        return float(stock.position_z_mm) + oz - float(stock.height_mm)
    return float(stock.position_z_mm) + oz


def _target_xy_for_origin(
    stock: Stock, wcs: WorkCoordinateSystem | None, desired_origin: object
) -> tuple[float, float]:
    desired = str(getattr(desired_origin, "value", desired_origin))
    ox = 0.0 if wcs is None else float(wcs.offset_x_mm)
    oy = 0.0 if wcs is None else float(wcs.offset_y_mm)
    if desired in ("center_xy_top_z", "center_xy_zero_z"):
        return (float(stock.position_x_mm) + ox, float(stock.position_y_mm) + oy)
    return (
        float(stock.position_x_mm) + ox + float(stock.width_mm) / 2.0,
        float(stock.position_y_mm) + oy + float(stock.length_mm) / 2.0,
    )


def _affine_core(
    bbox_center: tuple[float, float],
    placement: GeometryPlacement,
    dx: float,
    dy: float,
) -> Affine2D:
    cx, cy = bbox_center
    # Build list in application order: T(-c) -> Mx -> My -> Rz -> T(c) -> T(dx,dy)
    app_order: list[Affine2D] = [Affine2D.translate(-cx, -cy)]
    if placement.mirror_x:
        app_order.append(Affine2D.mirror_x())
    if placement.mirror_y:
        app_order.append(Affine2D.mirror_y())
    if placement.rotation_z_deg != 0.0:
        app_order.append(Affine2D.rotate(math.radians(float(placement.rotation_z_deg))))
    app_order.append(Affine2D.translate(cx, cy))
    app_order.append(Affine2D.translate(float(dx), float(dy)))
    # chain(*reversed) applies first element first
    rev = list(reversed(app_order))
    return Affine2D.chain(*rev)


def _is_empty(scene: GeometryScene) -> bool:
    return not any(True for _ in scene.iter_entities())


def affine_for_placement(
    placement: GeometryPlacement | None,
    scene: GeometryScene,
    stock: Stock,
    wcs: WorkCoordinateSystem | None = None,
) -> Affine2D:
    if placement is None:
        return Affine2D.identity()
    if _is_empty(scene):
        if placement.auto_center_xy:
            return Affine2D.identity()
        return Affine2D.translate(float(placement.offset_x_mm), float(placement.offset_y_mm))
    bbox = scene.bounding_box()
    # Placement is baked like SolidPlacement: stored offsets are honoured
    # regardless of auto_center flag (auto is audit, recompute only via
    # explicit Center preset which updates the stored offsets).
    dx = float(placement.offset_x_mm)
    dy = float(placement.offset_y_mm)
    return _affine_core((bbox.center.x, bbox.center.y), placement, dx, dy)


def apply_placement(
    scene: GeometryScene,
    placement: GeometryPlacement | None,
    stock: Stock | None = None,
    wcs: WorkCoordinateSystem | None = None,
) -> GeometryScene:
    if placement is None:
        return scene
    if stock is None:
        if _is_empty(scene):
            return scene
        bbox = scene.bounding_box()
        dx, dy = float(placement.offset_x_mm), float(placement.offset_y_mm)
        aff = _affine_core((bbox.center.x, bbox.center.y), placement, dx, dy)
        if aff == Affine2D.identity():
            return scene
        return scene.apply_affine(aff)
    aff = affine_for_placement(placement, scene, stock, wcs)
    if aff == Affine2D.identity():
        return scene
    return scene.apply_affine(aff)


def default_placement_for_scene(
    scene: GeometryScene,
    stock: Stock,
    wcs: WorkCoordinateSystem | None = None,
) -> GeometryPlacement:
    if _is_empty(scene):
        return GeometryPlacement(
            auto_center_xy=True, z_offset_from_top_mm=_DEFAULT_Z_OFFSET_MM, stock_origin=stock.origin
        )
    bbox = scene.bounding_box()
    scx, scy = _target_xy_for_origin(stock, wcs, stock.origin)
    dx = float(scx - bbox.center.x)
    dy = float(scy - bbox.center.y)
    return GeometryPlacement(
        offset_x_mm=float(dx),
        offset_y_mm=float(dy),
        z_offset_from_top_mm=_DEFAULT_Z_OFFSET_MM,
        rotation_z_deg=0.0,
        mirror_x=False,
        mirror_y=False,
        auto_center_xy=True,
        stock_origin=stock.origin,
    )


def translation_for_center_xy(
    scene: GeometryScene,
    stock: Stock,
    wcs: WorkCoordinateSystem | None = None,
    desired_origin: object | None = None,
) -> tuple[float, float]:
    if _is_empty(scene):
        return (0.0, 0.0)
    bbox = scene.bounding_box()
    if desired_origin is not None:
        scx, scy = _target_xy_for_origin(stock, wcs, desired_origin)
    else:
        scx, scy = stock_center_xy(stock, wcs)
    return (float(scx - bbox.center.x), float(scy - bbox.center.y))
