import logging
import logging.handlers
import sys
from pathlib import Path

from flowrecord.config import LOG_BACKUP_COUNT, LOG_DIR, LOG_MAX_BYTES, LOG_PATH

_NOISY_LOGGERS = ("pywinauto", "comtypes", "urllib3", "keyboard", "PIL", "matplotlib")

_CONSOLE_FMT = (
    "%(asctime)s.%(msecs)03d [%(levelname)-5s] [%(threadName)-10s] %(name)s: %(message)s"
)
_FILE_FMT = (
    "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(threadName)-14s | "
    "%(name)s | %(funcName)s:%(lineno)d | %(message)s"
)


def setup_logging(level: int = logging.DEBUG) -> Path:
    """Configure verbose logging: DEBUG to console and a rotating log file.

    Returns the path to the active log file.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(_CONSOLE_FMT, datefmt="%H:%M:%S"))
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(_FILE_FMT, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(file_handler)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.captureWarnings(True)

    boot = logging.getLogger("flowrecord")
    boot.debug(
        "Logging initialized — level=%s, file=%s, pid=%d",
        logging.getLevelName(level),
        LOG_PATH,
        __import__("os").getpid(),
    )
    return LOG_PATH
