"""Structured rotating application log.

Provides a single configured ``logging.Logger`` (``win-debloater``) that
writes to ``%LOCALAPPDATA%\\WinDebloater\\app.log`` via a size-bounded
rotating handler. PowerShell calls, batch worker results, and UI errors all
flow through this log; ``console=True`` builds also see the messages on
stderr.

Idempotent: calling ``setup`` more than once is safe (handlers are not
duplicated).
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.paths import user_data_dir

LOGGER_NAME = "win-debloater"
LOG_FILENAME = "app.log"
MAX_BYTES = 1 * 1024 * 1024  # 1 MB per file
BACKUP_COUNT = 5


def log_file_path() -> Path:
    """Absolute path of the rotating log file."""
    return user_data_dir() / LOG_FILENAME


def setup(level: int = logging.INFO, *, console: bool = True) -> logging.Logger:
    """Configure the shared logger (idempotent)."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    have_file = any(isinstance(h, RotatingFileHandler) for h in logger.handlers)
    have_stream = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        for h in logger.handlers
    )

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not have_file:
        try:
            fh = RotatingFileHandler(
                log_file_path(),
                maxBytes=MAX_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
            fh.setFormatter(formatter)
            fh.setLevel(level)
            logger.addHandler(fh)
        except OSError:
            # Read-only profile or path issue — fall back to stderr only.
            pass

    if console and not have_stream:
        sh = logging.StreamHandler(stream=sys.stderr)
        sh.setFormatter(formatter)
        sh.setLevel(level)
        logger.addHandler(sh)

    return logger


def get_logger() -> logging.Logger:
    """Return the shared application logger (auto-setup on first use)."""
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        setup()
    return logger
