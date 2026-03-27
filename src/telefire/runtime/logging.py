import logging
import sys


def build_logger(name: str, log_level: str = "info") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO if log_level == "info" else logging.DEBUG)

    if not any(getattr(handler, "_telefire_stdout", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._telefire_stdout = True
        logger.addHandler(handler)

    return logger
