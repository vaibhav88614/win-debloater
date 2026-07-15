"""QThread workers (require a QApplication)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.workers import BatchWorker, FnWorker  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_fn_worker_succeeded(qapp):
    """Run an FnWorker to completion and capture its result via the signal."""
    w = FnWorker(lambda a, b: a + b, 2, 3)
    loop = QEventLoop()
    results: list = []
    w.succeeded.connect(lambda v: (results.append(v), loop.quit()))
    QTimer.singleShot(5000, loop.quit)
    w.start()
    loop.exec()
    w.wait(3000)
    assert results == [5]


def test_fn_worker_succeeded_with_kwargs(qapp):
    w = FnWorker(lambda a, b: a * b, 4, b=5)
    loop = QEventLoop()
    results: list = []
    w.succeeded.connect(lambda v: (results.append(v), loop.quit()))
    QTimer.singleShot(5000, loop.quit)
    w.start()
    loop.exec()
    w.wait(3000)
    assert results == [20]


def test_fn_worker_failed(qapp):
    def boom():
        raise RuntimeError("nope")

    w = FnWorker(boom)
    loop = QEventLoop()
    errors = []
    w.failed.connect(lambda msg: (errors.append(msg), loop.quit()))
    QTimer.singleShot(5000, loop.quit)
    w.start()
    loop.exec()
    w.wait(3000)
    assert errors == ["nope"]


def test_batch_worker_reports_results(qapp):
    items = ["a", "b", "c"]

    def action(item):
        if item == "b":
            return False, "fail"
        return True, "ok"

    w = BatchWorker(items, action)
    loop = QEventLoop()
    per_item = []
    summary = {}
    w.item_done.connect(lambda it, ok, msg: per_item.append((it, ok, msg)))
    w.finished_all.connect(lambda s, t: (summary.update(success=s, total=t), loop.quit()))
    QTimer.singleShot(5000, loop.quit)
    w.start()
    loop.exec()
    w.wait(3000)

    assert summary == {"success": 2, "total": 3}
    assert per_item == [("a", True, "ok"), ("b", False, "fail"), ("c", True, "ok")]


def test_batch_worker_cancel_stops_iteration(qapp):
    items = list(range(10))

    def action(item):
        return True, str(item)

    w = BatchWorker(items, action)
    # Cancel before starting so iteration breaks on the very first item.
    w.cancel()
    loop = QEventLoop()
    summary = {}
    w.finished_all.connect(lambda s, t: (summary.update(success=s, total=t), loop.quit()))
    QTimer.singleShot(5000, loop.quit)
    w.start()
    loop.exec()
    w.wait(3000)

    assert summary == {"success": 0, "total": 10}


def test_batch_worker_logs_per_item_progress(qapp, caplog):
    """Every item start/end must land in the log at INFO so a stuck run is
    diagnosable without waiting for the batch to finish."""
    items = ["alpha", "beta"]

    def action(item):
        return True, f"processed {item}"

    caplog.set_level("INFO", logger="win-debloater")

    w = BatchWorker(items, action)
    loop = QEventLoop()
    w.finished_all.connect(lambda *_: loop.quit())
    QTimer.singleShot(5000, loop.quit)
    w.start()
    loop.exec()
    w.wait(3000)

    messages = [r.getMessage() for r in caplog.records]
    # Batch summary bookends.
    assert any("BatchWorker: starting 2 item" in m for m in messages), messages
    assert any("BatchWorker: finished 2/2" in m for m in messages), messages
    # Per-item start markers, so a stuck item is immediately obvious.
    # Strings don't have display_name/name, so BatchWorker falls back to
    # repr() -> label like "'alpha'" (which is fine and unambiguous in logs).
    assert any("[1/2] starting: 'alpha'" in m for m in messages), messages
    assert any("[2/2] starting: 'beta'" in m for m in messages), messages
    # Per-item outcome markers include OK/FAIL + elapsed for eyeball triage.
    assert any("[1/2] OK: 'alpha'" in m for m in messages), messages
    assert any("[2/2] OK: 'beta'" in m for m in messages), messages
