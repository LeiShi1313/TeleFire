import logging
from datetime import datetime
from pathlib import Path


class DailyFileLogger:
    def __init__(self, logger, formatter: logging.Formatter | None = None):
        self.logger = logger
        self.formatter = formatter or logging.Formatter("%(message)s")

    def attach(self, method: str, *segments: str, query=None):
        path = Path("logs").joinpath(method)
        for segment in segments:
            if segment:
                path = path.joinpath(segment)
        path.mkdir(parents=True, exist_ok=True)
        path = path.joinpath(
            f'{datetime.utcnow().strftime("%Y-%m-%d")}_[query={query if query else None}].log'
        )
        file_handler = logging.FileHandler(path.absolute())
        file_handler.setFormatter(self.formatter)
        self.logger.addHandler(file_handler)
        return file_handler
