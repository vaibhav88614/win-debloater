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
            if prev is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = prev
