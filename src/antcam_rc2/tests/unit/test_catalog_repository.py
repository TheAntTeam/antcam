"""Tests for read-only, validated machining catalog repositories."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from antcam_rc2.core.databases.repository import CatalogRepository
from antcam_rc2.core.errors import CatalogError, ConfigurationError


def _write_json(directory: Path, filename: str, items: list[dict[str, object]]) -> None:
    payload = {"schema_version": "1.0", "catalog_id": filename.removesuffix(".json"), "items": items}
    (directory / filename).write_text(json.dumps(payload), encoding="utf-8")


def write_catalog_bundle(directory: Path) -> None:
    _write_json(
        directory,
        "cooling.json",
        [
            {
                "id": "aerodust",
                "name": "AeroDust",
                "source_reference": "manufacturer documentation",
                "data_status": "verified",
                "kind": "air",
                "surface_speed_factor": 1.0,
                "chip_load_factor": 1.0,
            }
        ],
    )
    _write_json(
        directory,
        "machines.json",
        [
            {
                "id": "makera_z1",
                "name": "Makera Z1",
                "source_reference": "manufacturer documentation",
                "data_status": "verified",
                "vendor": "Makera",
                "model": "Z1",
                "catalog_version": "1.0",
                "work_area_x_mm": 200.0,
                "work_area_y_mm": 200.0,
                "work_area_z_mm": 100.0,
                "spindle_power_w": 150.0,
                "min_rpm": 0.0,
                "max_rpm": 13000.0,
                "max_feed_mm_min": 1000.0,
                "collet_sizes_mm": [3.175],
                "default_collet_size_mm": 3.175,
                "supported_cooling_ids": ["aerodust"],
                "default_cooling_id": "aerodust",
                "native_post": "makera",
                "spindle_diameter_mm": 52.0,
                "spindle_length_mm": 100.0,
            }
        ],
    )
    _write_json(
        directory,
        "tools.json",
        [
            {
                "id": "end_mill_3_175_2f",
                "name": "3.175 mm end mill",
                "source_reference": "tool manufacturer documentation",
                "data_status": "verified",
                "tool_type": "end_mill",
                "cutting_diameter_mm": 3.175,
                "shank_diameter_mm": 3.175,
                "flute_count": 2,
                "flute_length_mm": 12.0,
                "overall_length_mm": 38.0,
                "tool_material": "carbide",
            }
        ],
    )
    _write_json(
        directory,
        "materials.json",
        [
            {
                "id": "aluminum_6061",
                "name": "Aluminum 6061",
                "source_reference": "material machining handbook",
                "data_status": "verified",
                "family": "aluminum",
                "surface_speed_m_min": 250.0,
                "chip_load_mm_tooth": 0.025,
                "plunge_ratio": 0.4,
                "machinability_factor": 1.0,
            }
        ],
    )


def test_repository_loads_directory_once_and_resolves_catalog_records(tmp_path: Path) -> None:
    write_catalog_bundle(tmp_path)
    repository = CatalogRepository.from_directory(tmp_path)

    first_load = repository.load()
    second_load = repository.load()

    assert second_load is first_load
    assert repository.machine("makera_z1").name == "Makera Z1"
    assert repository.tool("end_mill_3_175_2f").flute_count == 2
    assert repository.material("aluminum_6061").family == "aluminum"
    assert repository.cooling("aerodust").kind == "air"
    assert repository.catalog_versions() == {
        "cooling": "1.0",
        "machines": "1.0",
        "materials": "1.0",
        "tools": "1.0",
    }


def test_repository_rejects_unknown_record_and_cross_catalog_references(tmp_path: Path) -> None:
    write_catalog_bundle(tmp_path)
    repository = CatalogRepository.from_directory(tmp_path)

    with pytest.raises(CatalogError, match="unknown machine") as error:
        repository.machine("unknown")
    assert error.value.code == "catalog_error"

    machines = json.loads((tmp_path / "machines.json").read_text(encoding="utf-8"))
    machines["items"][0]["supported_cooling_ids"] = ["flood"]
    machines["items"][0]["default_cooling_id"] = "flood"
    (tmp_path / "machines.json").write_text(json.dumps(machines), encoding="utf-8")

    invalid_repository = CatalogRepository.from_directory(tmp_path)
    with pytest.raises(ConfigurationError, match="unknown cooling"):
        invalid_repository.load()


def test_repository_rejects_malformed_or_schema_invalid_bundle(tmp_path: Path) -> None:
    write_catalog_bundle(tmp_path)
    repository = CatalogRepository.from_directory(tmp_path)
    (tmp_path / "machines.json").write_text("{ not json", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="invalid JSON"):
        repository.load()

    write_catalog_bundle(tmp_path)
    tools = json.loads((tmp_path / "tools.json").read_text(encoding="utf-8"))
    del tools["items"][0]["tool_type"]
    (tmp_path / "tools.json").write_text(json.dumps(tools), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="failed schema validation"):
        repository.load()
