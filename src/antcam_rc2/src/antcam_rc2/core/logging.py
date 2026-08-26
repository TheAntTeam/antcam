"""Structured logging setup for AntCAM RC2."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_ROOT_LOGGER_NAME = "antcam_rc2"
_FORMAT = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"


def setup_logger(
    name: str = _ROOT_LOGGER_NAME,
    level: int = logging.INFO,
    log_file: str | Path | None = None,
) -> logging.Logger:
    """Configure (idempotently) and return the named logger.

    Only the first call configures the handler(s); subsequent calls are no-ops
    unless the level changes. The console handler is always attached; a
    rotating file handler is added when ``log_file`` is provided.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        console.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(console)

        if log_file is not None:
            file_handler = RotatingFileHandler(
                Path(log_file),
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(logging.Formatter(_FORMAT))
            logger.addHandler(file_handler)

    logger.propagate = False
    return logger


def get_logger(name: str = _ROOT_LOGGER_NAME) -> logging.Logger:
    """Return a configured logger, configuring with defaults if needed."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        setup_logger(name)
    return logger
