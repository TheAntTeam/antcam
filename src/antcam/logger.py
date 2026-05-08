import logging
import os

"""Logging helpers used across the AntCAM package."""

def setup_logger(name="antcam", log_file="antcam.log", level=logging.DEBUG):
    """Create and configure a package logger.

    The logger writes INFO+ to console and DEBUG+ to file. If handlers already
    exist, the existing logger is returned to avoid duplicated output.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers when setup_logger is called multiple times.
    if logger.handlers:
        return logger

    # Console handler.
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch_formatter = logging.Formatter("%(levelname)s - %(message)s")
    ch.setFormatter(ch_formatter)

    # File handler.
    fh = logging.FileHandler(log_file, mode="w")
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    fh.setFormatter(fh_formatter)

    logger.addHandler(ch)
    logger.addHandler(fh)

    return logger
