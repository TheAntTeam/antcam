import os

import pytest

from antcam.feature_extractor import FeatureExtractor
from antcam.importer import Model


def _extract_hole_group_stats(step_path: str):
    model = Model.from_step(step_path)
    extractor = FeatureExtractor(model)
    features = extractor.extract()
    hole_groups = [f for f in features if getattr(f, "type", "") == "hole_group"]
    through = [f for f in hole_groups if f.props.get("through")]
    blind = [f for f in hole_groups if not f.props.get("through")]
    return len(hole_groups), len(through), len(blind)


@pytest.mark.parametrize(
    "step_path,min_hole_groups,min_through",
    [
        ("tests/data/bottle_opener.step", 1, 1),
        ("tests/data/flange.step", 1, 1),
    ],
)
def test_through_hole_detection_is_present(step_path, min_hole_groups, min_through):
    if not os.path.exists(step_path):
        pytest.skip(f"File {step_path} non trovato per il test")

    hole_groups, through, _ = _extract_hole_group_stats(step_path)
    assert hole_groups >= min_hole_groups
    assert through >= min_through


def test_mixed_through_and_blind_on_mounting_spider():
    step_path = "tests/data/mounting_spider.step"
    if not os.path.exists(step_path):
        pytest.skip(f"File {step_path} non trovato per il test")

    hole_groups, through, blind = _extract_hole_group_stats(step_path)
    assert hole_groups >= 2
    assert through >= 1
    assert blind >= 1


@pytest.mark.parametrize(
    "step_path",
    [
        "tests/data/bottle_opener.step",
        "tests/data/flange.step",
        "tests/data/mounting_spider.step",
    ],
)
def test_hole_group_through_consistency(step_path):
    if not os.path.exists(step_path):
        pytest.skip(f"File {step_path} non trovato per il test")

    model = Model.from_step(step_path)
    extractor = FeatureExtractor(model)
    features = extractor.extract()
    hole_groups = [f for f in features if getattr(f, "type", "") == "hole_group"]

    if not hole_groups:
        pytest.skip(f"Nessun hole_group estratto da {step_path}")

    for group in hole_groups:
        holes = getattr(group, "holes", [])
        assert holes, "Ogni hole_group deve contenere almeno un hole"
        assert group.props.get("count") == len(holes)

        through_values = {bool(h.props.get("through")) for h in holes}
        assert len(through_values) == 1, "I hole nel gruppo devono avere classificazione through coerente"
        assert bool(group.props.get("through")) == through_values.pop()


