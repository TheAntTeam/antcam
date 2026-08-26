"""Integration tests for the machining catalog bundle shipped with RC2."""

from __future__ import annotations

from antcam_rc2.app.application import Application
from antcam_rc2.core.databases.repository import CatalogRepository
from antcam_rc2.core.feeds_speeds import FeedSpeedRequest, FeedsSpeedsCalculator, OperationFamily


def test_packaged_catalogs_load_with_the_makera_z1_profile() -> None:
    repository = CatalogRepository.from_package()
    bundle = repository.load()
    machine = repository.machine("makera_z1")

    assert len(bundle.tools) >= 9
    assert len(bundle.materials) >= 4
    assert {"none", "aerodust", "mist", "flood"} <= set(bundle.cooling)
    assert machine.default_collet_size_mm == 3.175
    assert machine.max_rpm == 13000.0
    assert machine.default_cooling_id in bundle.cooling


def test_application_registers_catalog_repository_lazily(application: Application) -> None:
    repository = application.catalog_repository

    assert isinstance(repository, CatalogRepository)
    assert repository.machine("makera_z1").name == "Makera Z1"


def test_packaged_catalogs_produce_a_machine_bounded_feed_speed_result() -> None:
    repository = CatalogRepository.from_package()
    machine = repository.machine("makera_z1")
    result = FeedsSpeedsCalculator().calculate(
        FeedSpeedRequest(
            tool=repository.tool("end_mill_3_175_2f"),
            material=repository.material("aluminum_6061"),
            machine=machine,
            cooling=repository.cooling(machine.default_cooling_id),
            operation_family=OperationFamily.MILLING,
        )
    )

    assert max(machine.min_rpm, 1.0) <= result.rpm <= machine.max_rpm
    assert 0.0 < result.cut_feed_mm_min <= machine.max_feed_mm_min
    assert 0.0 < result.plunge_feed_mm_min <= machine.max_feed_mm_min
    assert result.origins["rpm"].value.startswith("automatic")
