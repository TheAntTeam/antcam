"""Helpers for non-destructive 3D solid placement (WCS-aware, stock-aware).

All functions are pure (no Qt, no I/O). The placement is stored as
``SolidPlacement`` in ``Project`` and applied lazily via ``Affine3D``.
"""

from __future__ import annotations

from antcam_rc2.core.geometry3d.scene import BoundingBox3D, SolidScene
from antcam_rc2.core.geometry3d.transform import Affine3D
from antcam_rc2.core.project.models import SolidPlacement, Stock, WorkCoordinateSystem

__all__ = [
    "apply_placement",
    "default_placement_for_scene",
    "stock_bottom_z",
    "stock_center",
    "stock_origin_point",
    "stock_top_z",
    "translation_after_snap_z",
    "translation_for_center_on_stock",
    "translation_for_desired_origin",
]


def stock_origin_point(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> tuple[float, float, float]:
    """Return the WCS-aware origin point for the given stock per ``StockOrigin``.

    This is the point designated by ``StockOrigin``:
    - CENTER_XY_*: the center of the stock footprint
    - CORNER_XY_*: the minimum corner of the stock footprint
    - *_TOP_Z: the top Z face
    - *_ZERO_Z: Z=0 (bottom face for zero_z variants)
    """
    ox = 0.0 if wcs is None else float(wcs.offset_x_mm)
    oy = 0.0 if wcs is None else float(wcs.offset_y_mm)
    oz = 0.0 if wcs is None else float(wcs.offset_z_mm)

    if stock.origin.value in ("center_xy_top_z", "center_xy_zero_z"):
        # Position is center XY
        origin_x = float(stock.position_x_mm) + ox
        origin_y = float(stock.position_y_mm) + oy
    else:
        # Position is corner XY
        origin_x = float(stock.position_x_mm) + ox
        origin_y = float(stock.position_y_mm) + oy

    if stock.origin.value in ("center_xy_top_z", "corner_xy_top_z"):
        # Top Z
        origin_z = float(stock.position_z_mm) + oz
    else:
        # Zero Z
        origin_z = 0.0

    return (origin_x, origin_y, origin_z)


def stock_center_xy(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> tuple[float, float]:
    """Return the WCS-aware center XY of the stock footprint."""
    ox = 0.0 if wcs is None else float(wcs.offset_x_mm)
    oy = 0.0 if wcs is None else float(wcs.offset_y_mm)
    if stock.origin.value in ("center_xy_top_z", "center_xy_zero_z"):
        return (float(stock.position_x_mm) + ox, float(stock.position_y_mm) + oy)
    # corner variants: position is corner
    return (
        float(stock.position_x_mm) + ox + float(stock.width_mm) / 2.0,
        float(stock.position_y_mm) + oy + float(stock.length_mm) / 2.0,
    )


def stock_top_z(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> float:
    """Return the WCS-aware top Z of the stock."""
    oz = 0.0 if wcs is None else float(wcs.offset_z_mm)
    if stock.origin.value in ("center_xy_top_z", "corner_xy_top_z"):
        return float(stock.position_z_mm) + oz
    # zero_z variants: top = bottom + height
    return float(stock.position_z_mm) + oz + float(stock.height_mm)


def stock_bottom_z(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> float:
    """Return the WCS-aware bottom Z of the stock."""
    oz = 0.0 if wcs is None else float(wcs.offset_z_mm)
    if stock.origin.value in ("center_xy_top_z", "corner_xy_top_z"):
        return float(stock.position_z_mm) + oz - float(stock.height_mm)
    return float(stock.position_z_mm) + oz


def stock_center(stock: Stock, wcs: WorkCoordinateSystem | None = None) -> tuple[float, float, float]:
    """Return the WCS-aware center of the stock (XYZ)."""
    cx, cy = stock_center_xy(stock, wcs)
    cz = (stock_top_z(stock, wcs) + stock_bottom_z(stock, wcs)) / 2.0
    return (cx, cy, cz)


def _translation_for_bbox(
    bbox: BoundingBox3D,
    stock: Stock,
    wcs: WorkCoordinateSystem | None,
    desired_origin: object,
) -> tuple[float, float, float]:
    """Compute translation to center XY and snap Z for a desired origin."""
    cx, cy, _cz = bbox.center
    ox = 0.0 if wcs is None else float(wcs.offset_x_mm)
    oy = 0.0 if wcs is None else float(wcs.offset_y_mm)
    oz = 0.0 if wcs is None else float(wcs.offset_z_mm)
    desired = str(getattr(desired_origin, "value", desired_origin))
    # XY: desired center variants use position as center, corner variants use position as corner.
    if desired in ("center_xy_top_z", "center_xy_zero_z"):
        target_x = float(stock.position_x_mm) + ox
        target_y = float(stock.position_y_mm) + oy
    else:
        target_x = float(stock.position_x_mm) + ox + float(stock.width_mm) / 2.0
        target_y = float(stock.position_y_mm) + oy + float(stock.length_mm) / 2.0
    dx = target_x - float(cx)
    dy = target_y - float(cy)
    # Z: TOP_Z snaps max at top, ZERO_Z snaps min at bottom.
    if desired in ("center_xy_top_z", "corner_xy_top_z"):
        top = float(stock.position_z_mm) + oz
        dz = float(top) - float(bbox.max_z)
    else:
        bottom = float(stock.position_z_mm) + oz
        dz = float(bottom) - float(bbox.min_z)
    return (float(dx), float(dy), float(dz))


def default_placement_for_scene(
    scene: SolidScene,
    stock: Stock,
    wcs: WorkCoordinateSystem | None = None,
) -> SolidPlacement:
    """Return a placement that centers XY on stock and snaps min-Z to stock top/zero per StockOrigin."""
    bbox = scene.bounding_box()
    if bbox.is_empty:
        return SolidPlacement(translation=(0.0, 0.0, 0.0), stock_origin=stock.origin)
    # Use the stock's own origin as desired.
    dx, dy, dz = _translation_for_bbox(bbox, stock, wcs, stock.origin)
    return SolidPlacement(translation=(float(dx), float(dy), float(dz)), stock_origin=stock.origin)


def translation_for_desired_origin(
    scene: SolidScene,
    stock: Stock,
    wcs: WorkCoordinateSystem | None,
    desired_origin: object,
) -> tuple[float, float, float]:
    """Public helper: translation to center XY / snap Z for an arbitrary desired origin."""
    bbox = scene.bounding_box()
    if bbox.is_empty:
        return (0.0, 0.0, 0.0)
    return _translation_for_bbox(bbox, stock, wcs, desired_origin)


def _rotated_bbox(scene: SolidScene, rotation_deg: tuple[float, float, float], scale: float) -> BoundingBox3D:
    """Bounding box of *scene* after rotation/scale about its original center (no translation)."""
    if rotation_deg == (0.0, 0.0, 0.0) and scale == 1.0:
        return scene.bounding_box()
    bbox = scene.bounding_box()
    if bbox.is_empty:
        return bbox
    rx, ry, rz = rotation_deg
    pivot = bbox.center
    aff = Affine3D.chain(
        Affine3D.translate(pivot[0], pivot[1], pivot[2]),
        Affine3D.rotate(rx, ry, rz),
        Affine3D.scale(scale) if scale != 1.0 else Affine3D.identity(),
        Affine3D.translate(-pivot[0], -pivot[1], -pivot[2]),
    )
    return scene.apply(aff).bounding_box()


def translation_for_center_on_stock(
    scene: SolidScene,
    stock: Stock,
    wcs: WorkCoordinateSystem | None = None,
    *,
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: float = 1.0,
) -> tuple[float, float, float]:
    """Translation that places the solid's center at the stock center (XYZ).

    Accounts for rotation/scale about the scene's original center so the result
    is correct even when the part is rotated.
    """
    bbox = scene.bounding_box()
    if bbox.is_empty:
        return (0.0, 0.0, 0.0)
    rbbox = _rotated_bbox(scene, rotation_deg, scale)
    scx, scy, scz = stock_center(stock, wcs)
    return (float(scx - rbbox.center[0]), float(scy - rbbox.center[1]), float(scz - rbbox.center[2]))


def translation_after_snap_z(
    scene: SolidScene,
    stock: Stock,
    wcs: WorkCoordinateSystem | None,
    current_placement: SolidPlacement | None,
    *,
    to_top: bool,
) -> tuple[float, float, float]:
    """Return a new translation that snaps Z to stock top/bottom, preserving XY.

    ``to_top=True`` aligns ``placed max_z`` to ``stock_top_z``; otherwise
    ``placed min_z`` to ``stock_bottom_z``. Rotation/scale are kept.
    """
    if current_placement is None:
        # No existing XY to preserve: snap with identity rotation/scale.
        bbox = scene.bounding_box()
        if bbox.is_empty:
            return (0.0, 0.0, 0.0)
        if to_top:
            dz = stock_top_z(stock, wcs) - float(bbox.max_z)
        else:
            dz = stock_bottom_z(stock, wcs) - float(bbox.min_z)
        # XY centered per stock
        cx, cy = stock_center_xy(stock, wcs)
        return (float(cx - bbox.center[0]), float(cy - bbox.center[1]), float(dz))
    placed = apply_placement(scene, current_placement)
    pb = placed.bounding_box()
    if pb.is_empty:
        return current_placement.translation
    tx, ty, tz = current_placement.translation
    target_z = stock_top_z(stock, wcs) if to_top else stock_bottom_z(stock, wcs)
    current_feature = float(pb.max_z) if to_top else float(pb.min_z)
    delta = float(target_z) - float(current_feature)
    return (float(tx), float(ty), float(tz + delta))


def placement_to_affine(placement: SolidPlacement | None) -> Affine3D:
    """Convert ``SolidPlacement`` to an ``Affine3D`` (identity if None)."""
    if placement is None:
        return Affine3D.identity()
    tx, ty, tz = placement.translation
    rx, ry, rz = placement.rotation_deg
    s = float(placement.scale)
    return Affine3D.from_translation_rotation_scale(tx, ty, tz, rx, ry, rz, s)


def placement_to_affine_with_pivot(
    placement: SolidPlacement | None, pivot: tuple[float, float, float] | None = None
) -> Affine3D:
    """Like :func:`placement_to_affine` but rotates/scales about ``pivot`` (bbox center)."""
    if placement is None:
        return Affine3D.identity()
    tx, ty, tz = placement.translation
    rx, ry, rz = placement.rotation_deg
    s = float(placement.scale)
    needs_pivot = (rx != 0.0 or ry != 0.0 or rz != 0.0 or s != 1.0) and pivot is not None
    if not needs_pivot:
        return Affine3D.from_translation_rotation_scale(tx, ty, tz, rx, ry, rz, s)
    px, py, pz = pivot
    # T(translation) * T(pivot) * R * S * T(-pivot)
    return Affine3D.chain(
        Affine3D.translate(tx, ty, tz),
        Affine3D.translate(px, py, pz),
        Affine3D.rotate(rx, ry, rz),
        Affine3D.scale(s) if s != 1.0 else Affine3D.identity(),
        Affine3D.translate(-px, -py, -pz),
    )


def apply_placement(scene: SolidScene, placement: SolidPlacement | None) -> SolidScene:
    """Return a new scene with ``placement`` applied (or original if None/identity)."""
    if placement is None:
        return scene
    # Rotation/scale should pivot about the original bbox center so the part
    # spins in place instead of orbiting about the world origin.
    bbox = scene.bounding_box()
    pivot = None if bbox.is_empty else bbox.center
    aff = placement_to_affine_with_pivot(placement, pivot)
    if aff.is_identity():
        return scene
    return scene.apply(aff)
