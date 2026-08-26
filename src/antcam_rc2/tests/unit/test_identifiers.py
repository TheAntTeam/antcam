"""Tests for core.identifiers."""

from __future__ import annotations

import pytest

from antcam_rc2.core.identifiers import is_valid_id, new_id


def test_new_id_has_prefix_and_suffix() -> None:
    value = new_id("op")
    assert value.startswith("op_")
    assert is_valid_id(value, "op")


def test_new_id_unique() -> None:
    ids = {new_id("op") for _ in range(1000)}
    assert len(ids) == 1000


def test_new_id_accepts_snake_prefix() -> None:
    value = new_id("hole_group")
    assert is_valid_id(value, "hole_group")


def test_invalid_prefix_raises() -> None:
    with pytest.raises(ValueError):
        new_id("")
    with pytest.raises(ValueError):
        new_id("op_")
    with pytest.raises(ValueError):
        new_id("9bad")
    with pytest.raises(ValueError):
        new_id("Op1")
    with pytest.raises(ValueError):
        new_id("hole__group")
    with pytest.raises(ValueError):
        new_id("_leading")


def test_is_valid_id_rejects_other_prefix() -> None:
    assert not is_valid_id("tool_a1b2c3d4", "op")


def test_is_valid_id_rejects_garbage() -> None:
    assert not is_valid_id("not-an-id", "op")
    assert not is_valid_id("op_XYZ12345", "op")
