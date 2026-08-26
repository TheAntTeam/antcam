"""Corpus-driven import regression tests (parametrized from the manifest)."""

from __future__ import annotations

import pytest

from tests.corpus.harness import check_scene_invariants, iter_fixtures


def _fixtures() -> list:
    return iter_fixtures()


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda fixture: fixture.name)
def test_import_invariants(fixture) -> None:
    from antcam_rc2.core.io import import_file

    scene = import_file(fixture.absolute_path())
    scene.normalize()  # imperial fixtures are checked in millimetres
    problems = check_scene_invariants(scene, fixture.expect)
    assert problems == [], f"invariant violations for {fixture.name}: {problems}"


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda fixture: fixture.name)
def test_import_normalizes_units(fixture) -> None:
    from antcam_rc2.core.io import import_file

    scene = import_file(fixture.absolute_path())
    if fixture.units == "imperial":
        assert scene.units.value == "imperial"
        scene.normalize()
        assert scene.units.value == "metric"
    else:
        assert scene.units.value == "metric"
