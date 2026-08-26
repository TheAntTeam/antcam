import logging
import sys


def setup_logger(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("antcam")
    logger.setLevel(level)

    if not logger.handlers:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        fmt = logging.Formatter("%(levelname)-8s %(name)s  %(message)s")
        console.setFormatter(fmt)
        logger.addHandler(console)

    return logger
