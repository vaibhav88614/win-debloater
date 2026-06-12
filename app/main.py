"""Application entry point.

Handles UAC self-elevation, then launches the PySide6 GUI.
"""
from __future__ import annotations

import sys
import os

# Allow running as `python -m app.main` or from a frozen exe.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.elevation import ensure_admin, is_admin  # noqa: E402


def main() -> int:
    # "--no-elevate" lets developers run without triggering UAC every launch.
    auto_elevate = "--no-elevate" not in sys.argv

    if not ensure_admin(auto_elevate=auto_elevate):
        # An elevated instance was launched; this one should exit.
        return 0

    # Import Qt lazily so elevation logic stays fast and dependency-light.
    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Windows Debloater & Task Control")
    app.setOrganizationName("win-debloater")

    window = MainWindow(is_elevated=is_admin())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
