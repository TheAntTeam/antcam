import logging
import os

def setup_logger(name="antcam", log_file="antcam.log", level=logging.DEBUG):
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Evita duplicati se già configurato
    if logger.handlers:
        return logger

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch_formatter = logging.Formatter("%(levelname)s - %(message)s")
    ch.setFormatter(ch_formatter)

    # File handler
    fh = logging.FileHandler(log_file, mode="w")
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    fh.setFormatter(fh_formatter)

    logger.addHandler(ch)
    logger.addHandler(fh)

    return logger
