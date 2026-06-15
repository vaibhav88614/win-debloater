"""Resource and data path resolution for dev and PyInstaller-frozen runs."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def base_dir() -> Path:
    """Directory containing bundled resources.

    When frozen by PyInstaller, resources live under ``sys._MEIPASS``.
    Otherwise they live next to the source tree (the project root).
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]  # set by PyInstaller
    # app/core/paths.py -> project root is two levels up from app/
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    """Return an absolute path to a bundled resource under the project tree."""
    return base_dir().joinpath(*parts)


def user_data_dir() -> Path:
    """Writable per-user directory for logs and action history."""
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = Path(root) / "WinDebloater"
    path.mkdir(parents=True, exist_ok=True)
    return path
