"""Corpus harness: loads the manifest and checks semantic invariants.

The manifest is the single source of truth for the regression corpus; the
harness compares only the fields explicitly present in each fixture's
``expect`` block, so fixtures survive numerical improvements (semantic
invariants, not byte-golden).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

CORPUS_DIR = Path(__file__).parent
MANIFEST_PATH = CORPUS_DIR / "manifest.json"


@dataclass(frozen=True, slots=True)
class CorpusFixture:
    """One manifest entry: path (relative to the corpus dir) + expectations."""

    name: str
    path: Path
    format: str
    units: str
    expect: dict

    def absolute_path(self) -> Path:
        return (CORPUS_DIR / self.path).resolve()


def load_manifest() -> dict:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise ValueError(f"unsupported corpus manifest schema: {payload.get('schema_version')}")
    return payload


def iter_fixtures() -> list[CorpusFixture]:
    manifest = load_manifest()
    fixtures: list[CorpusFixture] = []
    for item in manifest["fixtures"]:
        fixtures.append(
            CorpusFixture(
                name=item["name"],
                path=Path(item["path"]),
                format=item["format"],
                units=item["units"],
                expect=item.get("expect", {}),
            )
        )
    return fixtures


def check_scene_invariants(scene, expect: dict, *, tolerance: float = 0.01) -> list[str]:
    """Return invariant violations for one imported scene (empty = ok)."""
    problems: list[str] = []

    if "entities" in expect and len(scene.entities()) != expect["entities"]:
        problems.append(f"entities {len(scene.entities())} != {expect['entities']}")

    if "layers" in expect:
        actual = [layer.name for layer in scene.layers]
        if set(actual) != set(expect["layers"]):
            problems.append(f"layers {actual} != {expect['layers']}")

    if "bbox" in expect:
        box = scene.bounding_box()
        expected = expect["bbox"]
        actual = [box.min_x, box.min_y, box.max_x, box.max_y]
        if any(abs(a - b) > tolerance for a, b in zip(actual, expected, strict=True)):
            problems.append(f"bbox {actual} != {expected}")

    if "net_area_mm2" in expect:
        area = _net_area_mm2(scene)
        if abs(area - expect["net_area_mm2"]) > max(tolerance, expect["net_area_mm2"] * 0.02):
            problems.append(f"net area {area:.3f} != {expect['net_area_mm2']}")

    if "warnings_ge" in expect and len(scene.diagnostics.warnings) < expect["warnings_ge"]:
        problems.append(f"warnings {len(scene.diagnostics.warnings)} < {expect['warnings_ge']}")
    if "errors_ge" in expect and len(scene.diagnostics.errors) < expect["errors_ge"]:
        problems.append(f"errors {len(scene.diagnostics.errors)} < {expect['errors_ge']}")

    return problems


def _net_area_mm2(scene) -> float:
    from antcam_rc2.core.geometry.curves import Circle
    from antcam_rc2.core.geometry.paths import Contour, Path

    total = 0.0
    for _layer, entity in scene.iter_entities():
        if isinstance(entity, (Contour, Path)):
            total += abs(entity.area())
        elif isinstance(entity, Circle):
            total += math.pi * entity.radius**2
    return total


__all__ = ["CORPUS_DIR", "CorpusFixture", "check_scene_invariants", "iter_fixtures", "load_manifest"]
