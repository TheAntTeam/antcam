"""Fuzz tests: mutated DXF/SVG inputs must never crash unexpectedly."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).parent.parent.parent / "tools"
sys.path.insert(0, str(_TOOLS))

from fuzz_mutators import fuzz_import  # noqa: E402

CORPUS = Path(__file__).parent.parent / "corpus"
DATA = Path(__file__).parent.parent / "data"

FUZZ_TARGETS = [
    (CORPUS / "dxf" / "mech_plate.dxf", 60),
    (CORPUS / "svg" / "pcb_panel.svg", 60),
    (DATA / "mini_polyline_bulge.dxf", 40),
    (DATA / "mini_path.svg", 40),
]


_IDS = [f"{path.name}-{seeds}" for path, seeds in FUZZ_TARGETS]


@pytest.mark.parametrize("path,seeds", FUZZ_TARGETS, ids=_IDS)
def test_fuzz_import_never_crashes_unexpectedly(path: Path, seeds: int) -> None:
    problems = fuzz_import(path, range(seeds))
    assert problems == [], f"unexpected exceptions: {problems[:5]}"


def test_fuzz_truncated_binary_is_handled() -> None:
    """An empty / truncated payload must produce diagnostics, not a crash."""
    problems = fuzz_import(CORPUS / "dxf" / "mech_plate.dxf", range(8), max_bytes=64)
    assert problems == []


def test_mutator_is_deterministic() -> None:
    from fuzz_mutators import mutate

    source = (CORPUS / "svg" / "pcb_panel.svg").read_bytes()
    assert mutate(source, 7) == mutate(source, 7)
    assert mutate(source, 7) != mutate(source, 8)
