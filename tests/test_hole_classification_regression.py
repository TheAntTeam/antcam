import os
from collections import Counter

import pytest

from antcam.feature_extractor import FeatureExtractor
from antcam.importer import Model


def _extract_features(step_path: str):
    model = Model.from_step(step_path)
    extractor = FeatureExtractor(model)
    return extractor.extract()


def _extract_hole_group_stats(step_path: str):
    features = _extract_features(step_path)
    hole_groups = [f for f in features if getattr(f, "type", "") == "hole_group"]
    through = [f for f in hole_groups if f.props.get("through")]
    blind = [f for f in hole_groups if not f.props.get("through")]
    return len(hole_groups), len(through), len(blind)


def _feature_type_counts(step_path: str) -> Counter:
    return Counter(getattr(feature, "type", "unknown") for feature in _extract_features(step_path))


@pytest.mark.parametrize(
    "step_path,min_hole_groups,min_through",
    [
        ("tests/data/bottle_opener.step", 1, 1),
        ("tests/data/flange.step", 1, 1),
    ],
)
def test_through_hole_detection_is_present(step_path, min_hole_groups, min_through):
    if not os.path.exists(step_path):
        pytest.skip(f"Test fixture not found: {step_path}")

    hole_groups, through, _ = _extract_hole_group_stats(step_path)
    assert hole_groups >= min_hole_groups
    assert through >= min_through


def test_mixed_through_and_blind_on_mounting_spider():
    step_path = "tests/data/mounting_spider.step"
    if not os.path.exists(step_path):
        pytest.skip(f"Test fixture not found: {step_path}")

    hole_groups, through, blind = _extract_hole_group_stats(step_path)
    assert hole_groups >= 2
    assert through >= 1
    assert blind >= 1


def test_mounting_spider_retains_mixed_semantic_feature_types():
    step_path = "tests/data/mounting_spider.step"
    if not os.path.exists(step_path):
        pytest.skip(f"Test fixture not found: {step_path}")

    counts = _feature_type_counts(step_path)

    assert counts["pocket"] >= 10
    assert counts["slot"] >= 1
    assert counts["hole_group"] >= 3


def test_bottle_opener_retains_mixed_semantic_feature_types():
    step_path = "tests/data/bottle_opener.step"
    if not os.path.exists(step_path):
        pytest.skip(f"Test fixture not found: {step_path}")

    counts = _feature_type_counts(step_path)

    assert counts["pocket"] >= 2
    assert counts["slot"] >= 3
    assert counts["fillet"] >= 4
    assert counts["hole_group"] >= 1


def test_nema23_motor_bracket_retains_open_profile_semantics():
    step_path = "tests/data/nema23_motor_bracket.step"
    if not os.path.exists(step_path):
        pytest.skip(f"Test fixture not found: {step_path}")

    counts = _feature_type_counts(step_path)

    assert counts["chamfer"] >= 4
    assert counts["step"] >= 2
    assert counts["slot"] >= 3


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
        pytest.skip(f"Test fixture not found: {step_path}")

    features = _extract_features(step_path)
    hole_groups = [f for f in features if getattr(f, "type", "") == "hole_group"]

    if not hole_groups:
        pytest.skip(f"No hole_group extracted from {step_path}")

    for group in hole_groups:
        holes = getattr(group, "holes", [])
        assert holes, "Each hole_group must contain at least one hole"
        assert group.props.get("count") == len(holes)

        through_values = {bool(h.props.get("through")) for h in holes}
        assert len(through_values) == 1, "Holes within one group must agree on through classification"
        assert bool(group.props.get("through")) == through_values.pop()


