"""Tests for deterministic, stock-safe depth pass decomposition."""

from __future__ import annotations

import pytest

from antcam_rc2.core.errors import ToolpathError
from antcam_rc2.core.toolpath.depth_passes import plan_depth_passes


def test_depth_passes_end_exactly_on_target_without_exceeding_stepdown() -> None:
    passes = plan_depth_passes(top_z_mm=5.0, target_z_mm=-1.0, max_stepdown_mm=2.0, stock_bottom_z_mm=-2.0)

    assert passes == pytest.approx((3.0, 1.0, -1.0))
    assert all(previous - following <= 2.0 for previous, following in zip((5.0,) + passes[:-1], passes, strict=True))


@pytest.mark.parametrize(
    ("target_z_mm", "stepdown_mm", "stock_bottom_z_mm"),
    [(6.0, 1.0, -2.0), (-3.0, 1.0, -2.0), (0.0, 0.0, -2.0)],
)
def test_depth_passes_reject_unsafe_or_degenerate_requests(
    target_z_mm: float, stepdown_mm: float, stock_bottom_z_mm: float
) -> None:
    with pytest.raises(ToolpathError):
        plan_depth_passes(
            top_z_mm=5.0,
            target_z_mm=target_z_mm,
            max_stepdown_mm=stepdown_mm,
            stock_bottom_z_mm=stock_bottom_z_mm,
        )
