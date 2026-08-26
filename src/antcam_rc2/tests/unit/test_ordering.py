"""Tests for the deterministic nearest-neighbour path ordering."""

from __future__ import annotations

import pytest

from antcam_rc2.core.geometry.curves import LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.toolpath.ordering import (
    MAX_VECTORIZED_PATHS,
    order_contours_by_fingerprint,
    order_islands_nearest_neighbor,
    reorder_pass_contours,
)


def make_line_contour(x: float, y: float) -> Contour:
    return Contour([LineSegment(Point2(x, y), Point2(x + 1.0, y))])


def test_ordering_is_deterministic_and_prefers_nearest() -> None:
    entities = [make_line_contour(10.0, 0.0), make_line_contour(0.0, 0.0), make_line_contour(0.5, 0.0)]
    first, _ = order_islands_nearest_neighbor(list(entities))
    second, _ = order_islands_nearest_neighbor(list(entities))
    assert [id(e) for e in first] == [id(e) for e in second]
    # The tour starts at the first entity's own point (distance 0), then jumps
    # to the nearest remaining (0.5, 0) and finally (0, 0).
    assert first == [entities[0], entities[2], entities[1]]


def test_ordering_tie_break_prefers_input_index() -> None:
    a = make_line_contour(0.0, 0.0)
    b = make_line_contour(1.0, 0.0)
    c = make_line_contour(1.0, 0.0)  # identical start point to b
    ordered, _ = order_islands_nearest_neighbor([a, c, b])
    # After a, both b and c are equidistant; the first in input order wins.
    assert ordered == [a, c, b]


def test_vectorized_path_matches_brute_force_for_large_sets() -> None:
    entities = [make_line_contour((i * 37) % 97, (i * 13) % 61) for i in range(150)]
    ordered, fallback = order_islands_nearest_neighbor(list(entities))
    assert not fallback
    assert len(ordered) == len(entities)
    assert set(id(e) for e in ordered) == set(id(e) for e in entities)


def test_fallback_to_input_order_beyond_limit() -> None:
    entities = [make_line_contour(i, 0.0) for i in range(MAX_VECTORIZED_PATHS + 1)]
    ordered, fallback = order_islands_nearest_neighbor(list(entities))
    assert fallback
    assert [id(e) for e in ordered] == [id(e) for e in entities]


def test_fingerprint_order_is_stable() -> None:
    contours = [make_line_contour(3.0, 0.0), make_line_contour(1.0, 0.0), make_line_contour(2.0, 0.0)]
    assert order_contours_by_fingerprint(contours) == order_contours_by_fingerprint(list(reversed(contours)))


def test_reorder_pass_contours_keeps_type() -> None:
    contours = [make_line_contour(3.0, 0.0), make_line_contour(1.0, 0.0)]
    ordered = reorder_pass_contours(contours)
    assert all(isinstance(c, Contour) for c in ordered)


def test_entity_start_and_end_points_for_path_and_curve() -> None:
    from antcam_rc2.core.geometry.curves import LineSegment
    from antcam_rc2.core.geometry.paths import Path
    from antcam_rc2.core.toolpath.ordering import entity_end_point, entity_start_point

    line = LineSegment(Point2(1.0, 2.0), Point2(3.0, 4.0))
    path = Path([make_line_contour(5.0, 6.0)])
    assert entity_start_point(line) == Point2(1.0, 2.0)
    assert entity_end_point(line) == Point2(3.0, 4.0)
    assert entity_start_point(path) == Point2(5.0, 6.0)
    assert entity_end_point(path) == Point2(6.0, 6.0)


def test_entity_points_reject_unsupported_entities() -> None:
    from antcam_rc2.core.toolpath.ordering import entity_start_point

    with pytest.raises(ValueError):
        entity_start_point(object())  # ty: ignore[invalid-argument-type]


def test_distance_squared() -> None:
    from antcam_rc2.core.toolpath.ordering import distance_squared

    assert distance_squared(Point2(0.0, 0.0), Point2(3.0, 4.0)) == 25.0


def test_empty_input_returns_empty() -> None:
    ordered, fallback = order_islands_nearest_neighbor([])
    assert ordered == []
    assert not fallback
