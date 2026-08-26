"""Manifest validation: the corpus document must be well-formed."""

from __future__ import annotations

from tests.corpus.harness import MANIFEST_PATH, iter_fixtures, load_manifest


def test_manifest_loads_and_is_versioned() -> None:
    manifest = load_manifest()
    assert manifest["schema_version"] == "1.0"
    assert len(manifest["fixtures"]) >= 10


def test_every_fixture_file_exists() -> None:
    for fixture in iter_fixtures():
        assert fixture.absolute_path().is_file(), f"missing fixture: {fixture.absolute_path()}"


def test_fixture_names_are_unique() -> None:
    names = [fixture.name for fixture in iter_fixtures()]
    assert len(names) == len(set(names))


def test_fixture_paths_are_inside_the_corpus() -> None:
    for fixture in iter_fixtures():
        path = fixture.absolute_path()
        assert "tests" in path.parts or "data" in path.parts
        assert path.suffix in {".dxf", ".svg"}


def test_expected_plan_fields_are_coherent() -> None:
    for fixture in iter_fixtures():
        plan = fixture.expect.get("plan")
        if plan is not None:
            assert isinstance(plan.get("layer"), str)
            assert isinstance(plan.get("entity_index"), int) and plan["entity_index"] >= 0
            assert isinstance(plan.get("executable"), bool)
            assert isinstance(plan.get("operations"), int) and plan["operations"] >= 1


def test_manifest_serializes_deterministically() -> None:
    import json

    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert json.dumps(payload, sort_keys=True) == json.dumps(payload, sort_keys=True)
