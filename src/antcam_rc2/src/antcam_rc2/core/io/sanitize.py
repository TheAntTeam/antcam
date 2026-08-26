"""Post-import numeric sanitization shared by the DXF and SVG importers.

Coordinates that are NaN or infinite would poison geometry downstream (planning,
simulation, rendering); such entities are dropped with an explicit diagnostic.
Extremely large coordinates (possible m/mm confusion) are kept but warned once.
"""

from __future__ import annotations

import math

from antcam_rc2.core.geometry.curves import Arc, Circle, Curve2, LineSegment
from antcam_rc2.core.geometry.paths import Contour, Path
from antcam_rc2.core.io.scene import GeometryScene

_MAX_COORDINATE_MM = 1e6


def sanitize_scene(scene: GeometryScene) -> GeometryScene:
    """Remove entities with non-finite coordinates; warn on extremes.

    Operates in place on the scene's layers and returns the same scene for
    convenience.  Called at the end of every importer.
    """
    warned_extreme = False
    for layer in scene.layers:
        kept: list = []
        for entity in layer.entities:
            finite = _coords_finite(entity)
            if not finite:
                scene.diagnostics.add_warning(
                    f"{type(entity).__name__} with non-finite coordinates dropped",
                    entity=layer.name,
                )
                continue
            if not warned_extreme and _coords_extreme(entity):
                warned_extreme = True
                scene.diagnostics.add_warning(
                    "coordinates exceed 1e6 mm — possible m/mm unit confusion",
                    entity=layer.name,
                )
            kept.append(entity)
        layer.entities = kept
    return scene


def _coords_finite(entity) -> bool:
    """True when every coordinate of an entity is finite."""
    if isinstance(entity, LineSegment):
        return _finite(entity.start) and _finite(entity.end)
    if isinstance(entity, Circle):
        return _finite(entity.center) and math.isfinite(entity.radius)
    if isinstance(entity, Arc):
        return (
            _finite(entity.center)
            and math.isfinite(entity.radius)
            and math.isfinite(entity.start_angle)
            and math.isfinite(entity.end_angle)
        )
    if isinstance(entity, Contour):
        return all(_coords_finite(segment) for segment in entity.segments)
    if isinstance(entity, Path):
        return all(_coords_finite(contour) for contour in entity.contours)
    if isinstance(entity, Curve2):
        return _finite(entity.start) and _finite(entity.end)
    return False


def _coords_extreme(entity) -> bool:
    """True when any coordinate exceeds the 1e6 mm sanity bound."""
    if isinstance(entity, Path):
        return any(_coords_extreme(contour) for contour in entity.contours)
    if isinstance(entity, Contour):
        return any(_coords_extreme(segment) for segment in entity.segments)
    if isinstance(entity, LineSegment):
        return _extreme(entity.start) or _extreme(entity.end)
    if isinstance(entity, Circle):
        return _extreme(entity.center) or abs(entity.radius) > _MAX_COORDINATE_MM
    if isinstance(entity, Arc):
        return _extreme(entity.center) or abs(entity.radius) > _MAX_COORDINATE_MM
    if isinstance(entity, Curve2):
        return _extreme(entity.start) or _extreme(entity.end)
    return False


def _finite(point) -> bool:
    return math.isfinite(point.x) and math.isfinite(point.y)


def _extreme(point) -> bool:
    return abs(point.x) > _MAX_COORDINATE_MM or abs(point.y) > _MAX_COORDINATE_MM


__all__ = ["sanitize_scene"]
