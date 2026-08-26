"""Tests for core.config."""

from __future__ import annotations

from pathlib import Path

from antcam_rc2.core.config import AppConfig, load_config


def test_defaults() -> None:
    config = AppConfig()
    assert config.log_level == "INFO"
    assert config.log_file is None
    assert config.user_data_dir == Path.home() / ".antcam_rc2"


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("ANTCAM_LOG_LEVEL", "DEBUG")
    config = AppConfig()
    assert config.log_level == "DEBUG"


def test_load_config(monkeypatch) -> None:
    monkeypatch.setenv("ANTCAM_LOG_LEVEL", "ERROR")
    config = load_config()
    assert config.log_level == "ERROR"


def test_path_expansion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTCAM_USER_DATA_DIR", str(tmp_path / "user"))
    config = AppConfig()
    assert config.user_data_dir == tmp_path / "user"
    assert config.cache_dir == tmp_path / "user" / "cache"


def test_cache_dir_relative_to_user_dir() -> None:
    config = AppConfig(user_data_dir=Path("/tmp/ant"))
    assert config.cache_dir == Path("/tmp/ant") / "cache"
