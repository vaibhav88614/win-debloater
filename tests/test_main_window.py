"""End-to-end GUI bootstrap test."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_builds_with_all_tabs(qapp):
    from app.ui.main_window import MainWindow

    w = MainWindow(is_elevated=False)
    try:
        names = [w.tabs.tabText(i) for i in range(w.tabs.count())]
        assert names == [
            "Bloatware",
            "Services",
            "Scheduled Tasks",
            "Processes & Suspicious",
            "History",
        ]
        # Header toggles should exist and default sensibly.
        assert w.advanced_toggle.isChecked() is False
        assert w.restore_toggle.isChecked() is True
        # AppContext is wired up.
        assert w.ctx.is_advanced() is False
        assert w.ctx.want_restore_point() is True
    finally:
        w.close()
