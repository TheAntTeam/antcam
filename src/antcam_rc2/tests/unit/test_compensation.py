"""Tests for tool-centre compensation: offsets, pocket loops, raster and slots."""

from __future__ import annotations

import pytest

from antcam_rc2.core.errors import ToolpathError
from antcam_rc2.core.geometry.curves import LineSegment
from antcam_rc2.core.geometry.paths import Contour
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.toolpath.compensation import (
    facing_region_offset,
    pocket_offsets,
    profile_offset,
    slot_geometry,
    validate_tool_fits,
)


def make_square(x0: float = 0.0, y0: float = 0.0, size: float = 20.0) -> Contour:
    points = [Point2(x0, y0), Point2(x0 + size, y0), Point2(x0 + size, y0 + size), Point2(x0, y0 + size)]
    return Contour([LineSegment(a, b) for a, b in zip(points, points[1:] + [points[0]], strict=True)])


def make_slot(width: float = 20.0, height: float = 2.0) -> Contour:
    """A rectangular slot contour (width x height)."""
    points = [Point2(0.0, 0.0), Point2(width, 0.0), Point2(width, height), Point2(0.0, height)]
    return Contour([LineSegment(a, b) for a, b in zip(points, points[1:] + [points[0]], strict=True)])


def test_profile_offset_outside_expands_and_inside_shrinks(end_mill) -> None:
    square = make_square()
    outside = profile_offset(square, end_mill, "outside")
    inside = profile_offset(square, end_mill, "inside")
    assert outside is not None and inside is not None
    assert outside.bounding_box().width > square.bounding_box().width
    assert inside.bounding_box().width < square.bounding_box().width


def test_profile_offset_degenerates_for_tool_larger_than_region(end_mill) -> None:
    tiny = make_square(size=1.0)
    assert profile_offset(tiny, end_mill, "inside") is None


def test_pocket_offsets_generate_inward_loops(end_mill) -> None:
    loops = pocket_offsets(make_square(), end_mill, stepover_mm=2.0)
    assert len(loops) >= 3
    widths = [loop.bounding_box().width for loop in loops]
    assert widths == sorted(widths, reverse=True)
    assert loops[0].bounding_box().width < 20.0


def test_pocket_offsets_reject_tool_larger_than_region(end_mill) -> None:
    with pytest.raises(ToolpathError, match="too small"):
        pocket_offsets(make_square(size=1.0), end_mill, stepover_mm=1.0)


def test_pocket_offsets_reject_bad_stepover(end_mill) -> None:
    with pytest.raises(ToolpathError, match="positive"):
        pocket_offsets(make_square(), end_mill, stepover_mm=0.0)


def test_facing_raster_generates_parallel_lines(end_mill) -> None:
    lines = facing_region_offset(make_square(), end_mill, stepover_mm=3.0)
    assert lines
    ys = sorted(line.start.y for line in lines)
    assert all(abs(b - a - 3.0) <= 1e-6 for a, b in zip(ys, ys[1:], strict=False))
    assert all(abs(line.start.y - line.end.y) <= 1e-9 for line in lines)


def test_facing_raster_rejects_region_smaller_than_tool(end_mill) -> None:
    with pytest.raises(ToolpathError):
        facing_region_offset(make_square(size=0.5), end_mill, stepover_mm=1.0)


def test_slot_geometry_centerline_for_narrow_slot(end_mill) -> None:
    slot = make_slot(width=20.0, height=3.2)  # wider than the tool, single pass
    lanes = slot_geometry(slot, end_mill, stepover_mm=1.0)
    assert len(lanes) == 1
    assert len(lanes[0]) == 2
    # Centerline sits at the middle of the slot's long axis.
    assert abs(lanes[0][0].y - 1.6) <= 1e-9


def test_slot_geometry_multi_lane_for_wide_slot(end_mill) -> None:
    slot = make_slot(width=20.0, height=10.0)
    lanes = slot_geometry(slot, end_mill, stepover_mm=3.0)
    assert len(lanes) >= 2


def test_slot_geometry_rejects_slot_narrower_than_tool(end_mill) -> None:
    slot = make_slot(width=20.0, height=1.0)
    with pytest.raises(ToolpathError, match="narrower"):
        slot_geometry(slot, end_mill, stepover_mm=1.0)


def test_validate_tool_fits_rejects_oversized_tool(end_mill) -> None:
    fits, error = validate_tool_fits(make_square(size=1.0), end_mill)
    assert not fits
    assert error is not None
    assert validate_tool_fits(make_square(size=20.0), end_mill)[0] is True


def test_profile_offset_rejects_multiple_result_loops(end_mill, monkeypatch) -> None:
    import antcam_rc2.core.toolpath.compensation as compensation_module

    def fake_offset(contour, distance, **kwargs):
        return [contour, contour]  # simulate a split offset

    monkeypatch.setattr(compensation_module, "offset", fake_offset)
    with pytest.raises(ToolpathError, match="loops"):
        profile_offset(make_square(), end_mill, "outside")


def test_slot_centerline_returns_none_for_unusable_slots(end_mill) -> None:
    from antcam_rc2.core.toolpath.compensation import slot_centerline

    assert slot_centerline(make_slot(width=20.0, height=10.0), end_mill) is None  # multi-lane
    assert slot_centerline(make_slot(width=20.0, height=0.5), end_mill) is None  # too narrow
    assert slot_centerline(make_slot(width=20.0, height=3.2), end_mill) is not None  # single centerline


def test_slot_geometry_rejects_open_or_bad_stepover(end_mill) -> None:
    open_contour = Contour([LineSegment(Point2(0.0, 0.0), Point2(10.0, 0.0))])
    with pytest.raises(ToolpathError, match="closed"):
        slot_geometry(open_contour, end_mill, stepover_mm=1.0)
    with pytest.raises(ToolpathError, match="positive"):
        slot_geometry(make_square(), end_mill, stepover_mm=0.0)
