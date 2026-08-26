"""Local island/path ordering optimizer for single operations.

The nearest-neighbour ordering is deterministic (ties are broken by the input
index) and vectorized with numpy for large path counts.  Past
``MAX_VECTORIZED_PATHS`` the input order is preserved and a fallback flag is
reported, keeping the planner O(n log n) worst case instead of unbounded O(n²).
"""

from __future__ import annotations

import numpy as np

from antcam_rc2.core.geometry.curves import Curve2
from antcam_rc2.core.geometry.paths import Contour, Path
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.scene import SceneEntity

MAX_VECTORIZED_PATHS = 2000


def contour_start_point(contour: Contour) -> Point2:
    """Get the start point of a contour."""
    return contour.start


def contour_end_point(contour: Contour) -> Point2:
    """Get the end point of a contour."""
    return contour.end


def entity_start_point(entity: SceneEntity) -> Point2:
    """Get the start point of a scene entity."""
    if isinstance(entity, Contour):
        return contour_start_point(entity)
    if isinstance(entity, Path):
        if entity.contours:
            return contour_start_point(entity.contours[0])
        raise ValueError(f"cannot get start point of empty {type(entity)}")
    if isinstance(entity, Curve2):
        return entity.start
    raise ValueError(f"cannot get start point of {type(entity)}")


def entity_end_point(entity: SceneEntity) -> Point2:
    """Get the end point of a scene entity."""
    if isinstance(entity, Contour):
        return contour_end_point(entity)
    if isinstance(entity, Path):
        if entity.contours:
            return contour_end_point(entity.contours[-1])
        raise ValueError(f"cannot get end point of empty {type(entity)}")
    if isinstance(entity, Curve2):
        return entity.end
    raise ValueError(f"cannot get end point of {type(entity)}")


def distance_squared(p1: Point2, p2: Point2) -> float:
    """Squared distance between two points."""
    dx = p1.x - p2.x
    dy = p1.y - p2.y
    return dx * dx + dy * dy


def order_islands_nearest_neighbor[T: SceneEntity](
    entities: list[T],
    start_from: Point2 | None = None,
    max_paths: int = MAX_VECTORIZED_PATHS,
) -> tuple[list[T], bool]:
    """Order islands/contours using deterministic vectorized nearest-neighbour.

    Returns ``(ordered_entities, fallback_used)``.  When the input exceeds
    ``max_paths`` the input order is preserved and ``fallback_used`` is True.
    """
    if not entities:
        return [], False
    count = len(entities)
    if count > max_paths:
        return list(entities), True

    starts = np.empty((count, 2), dtype=np.float64)
    ends = np.empty((count, 2), dtype=np.float64)
    for index, entity in enumerate(entities):
        start = entity_start_point(entity)
        end = entity_end_point(entity)
        starts[index, 0] = start.x
        starts[index, 1] = start.y
        ends[index, 0] = end.x
        ends[index, 1] = end.y

    if start_from is None:
        current = starts[0].copy()
    else:
        current = np.array([start_from.x, start_from.y], dtype=np.float64)

    remaining = list(range(count))
    ordered_indices: list[int] = []
    while remaining:
        deltas = starts[remaining] - current
        distances = deltas[:, 0] * deltas[:, 0] + deltas[:, 1] * deltas[:, 1]
        # np.argmin returns the first minimal index → deterministic tie-break.
        best_position = int(np.argmin(distances))
        best_index = remaining[best_position]
        ordered_indices.append(best_index)
        current = ends[best_index]
        remaining.pop(best_position)

    return [entities[index] for index in ordered_indices], False


def order_contours_nearest_neighbor(
    contours: list[Contour],
    start_from: Point2 | None = None,
    max_paths: int = MAX_VECTORIZED_PATHS,
) -> tuple[list[Contour], bool]:
    """Order contours with the deterministic vectorized nearest-neighbour.

    ``Contour``-typed variant of :func:`order_islands_nearest_neighbor` for
    callers that must keep the concrete element type (motion tracers).
    """
    ordered, fallback = order_islands_nearest_neighbor(contours, start_from, max_paths)
    return [contour for contour in ordered if isinstance(contour, Contour)], fallback


def order_contours_by_fingerprint(
    contours: list[Contour],
) -> list[Contour]:
    """Deterministic tie-break using the geometric fingerprint."""

    def fingerprint(contour: Contour) -> str:
        bbox = contour.bounding_box()
        return f"{bbox.min_x:.6f},{bbox.min_y:.6f},{bbox.max_x:.6f},{bbox.max_y:.6f},{contour.area():.6f}"

    return sorted(contours, key=fingerprint)


def reorder_pass_contours(
    contours: list[Contour],
    previous_pass_end: Point2 | None = None,
) -> list[Contour]:
    """Reorder contours within a single depth pass for optimal travel."""
    if not contours:
        return contours
    ordered, _ = order_contours_nearest_neighbor(contours, previous_pass_end)
    return ordered
