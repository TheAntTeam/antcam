"""Opt-in performance tests (skip unless run with ``--benchmark``).

These are micro-benchmarks that assert loose sanity bounds (an order of
magnitude above expected) — the authoritative budgets live in
``tools/benchmarks.json`` enforced by the CI ``bench`` job.
"""

from __future__ import annotations

import statistics
import time

import pytest

from antcam_rc2.core.geometry.curves import LineSegment
from antcam_rc2.core.geometry.primitives import Point2
from antcam_rc2.core.io.scene import GeometryScene, SourceInfo
from antcam_rc2.core.project.geometry_refs import build_scene_index


@pytest.mark.benchmark
def test_scene_index_build_stays_fast() -> None:
    scene = GeometryScene(source=SourceInfo(format="dxf", path="bench.dxf"))
    for index in range(2000):
        scene.add_entity(
            LineSegment(Point2(float(index), 0.0), Point2(float(index) + 1.0, 1.0)),
            "lines",
        )
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        build_scene_index(scene)
        timings.append(time.perf_counter() - start)
    median = statistics.median(timings)
    # 2000 entities should index well under 1 s even on slow CI runners.
    assert median < 1.0


@pytest.mark.benchmark
def test_import_mech_plate_stays_fast() -> None:
    from pathlib import Path

    from antcam_rc2.core.io import import_file

    fixture = Path(__file__).parent.parent / "corpus" / "dxf" / "mech_plate.dxf"
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        import_file(fixture)
        timings.append(time.perf_counter() - start)
    assert statistics.median(timings) < 1.0
