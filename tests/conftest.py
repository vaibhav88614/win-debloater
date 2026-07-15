"""Shared pytest configuration.

- Adds the project root to ``sys.path`` so the ``app`` package imports.
- Forces Qt to use the offscreen platform so GUI tests run headlessly.
- Redirects ``LOCALAPPDATA`` to a temp directory for the whole test session
  so the on-disk action log never touches the real user profile.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def _isolate_user_data_dir():
    """Point ``user_data_dir`` at a throw-away folder for the whole session."""
    with tempfile.TemporaryDirectory(prefix="windebloater-tests-") as tmp:
        prev = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = tmp
        try:
            yield tmp
        finally:
            # Close rotating-log handlers so Windows releases the file before
            # the temp directory is removed.
            import logging

            for name in ("win-debloater",):
                logger = logging.getLogger(name)
                for h in list(logger.handlers):
                    try:
                        h.close()
                    except Exception:  # noqa: BLE001
                        pass
                    logger.removeHandler(h)
            if prev is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = prev


@pytest.fixture(autouse=True)
def _propagate_app_logger():
    """Enable log propagation on the ``win-debloater`` logger for the duration
    of each test. The production code sets ``propagate = False`` so records
    don't double-log in the frozen app, but pytest's ``caplog`` attaches its
    handler to the *root* logger — so without propagation, tests can't inspect
    what our modules log. Restore the original value afterwards.
    """
    import logging

    logger = logging.getLogger("win-debloater")
    prev = logger.propagate
    logger.propagate = True
    try:
        yield
    finally:
        logger.propagate = prev
