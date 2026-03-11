import logging
from pathlib import Path

LOG_PATH = Path("bot.log")
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

_configured = False
_error_counter = None


class ErrorCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.error_count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self.error_count += 1


def configure_logging() -> None:
    global _configured, _error_counter
    if _configured:
        return

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)

    root_logger = logging.getLogger()

    _error_counter = ErrorCounter()
    root_logger.addHandler(_error_counter)

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(file_handler)

    _configured = True


def get_error_count() -> int:
    if _error_counter is None:
        return 0
    return int(_error_counter.error_count)
