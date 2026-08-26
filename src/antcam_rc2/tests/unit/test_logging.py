"""Tests for core.logging."""

from __future__ import annotations

import logging
from pathlib import Path

from antcam_rc2.core.logging import get_logger, setup_logger


def test_setup_logger_single_handler() -> None:
    logger = setup_logger("test.unique.single", level=logging.INFO)
    assert len(logger.handlers) == 1
    # Idempotent: a second call must not duplicate handlers.
    setup_logger("test.unique.single", level=logging.INFO)
    assert len(logger.handlers) == 1
    logger.handlers.clear()


def test_setup_logger_file_handler(tmp_path: Path) -> None:
    log_file = tmp_path / "antcam.log"
    logger = setup_logger("test.unique.file", level=logging.DEBUG, log_file=log_file)
    assert len(logger.handlers) == 2
    logger.debug("hello")
    for handler in logger.handlers:
        handler.flush()
    assert log_file.exists()
    assert "hello" in log_file.read_text(encoding="utf-8")
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()


def test_get_logger_configures_defaults() -> None:
    logger = get_logger("test.unique.getter")
    assert logger.handlers
    logger.handlers.clear()


def test_get_logger_existing_preserved() -> None:
    name = "test.unique.existing"
    setup_logger(name, level=logging.ERROR)
    logger = get_logger(name)
    assert logger.level == logging.ERROR
    logger.handlers.clear()
