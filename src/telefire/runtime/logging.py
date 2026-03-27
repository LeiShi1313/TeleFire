import logging
import sys


class SafeStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            super().emit(record)
        except BrokenPipeError:
            pass


def build_logger(name: str, log_level: str = "info") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO if log_level == "info" else logging.DEBUG)

    if not any(getattr(handler, "_telefire_stdout", False) for handler in logger.handlers):
        handler = SafeStreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._telefire_stdout = True
        logger.addHandler(handler)

    return logger
