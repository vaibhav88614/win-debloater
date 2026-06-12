"""QThread workers so backend calls never block the UI."""
from __future__ import annotations

from typing import Any, Callable, Iterable

from PySide6.QtCore import QThread, Signal


class FnWorker(QThread):
    """Run a single callable in a background thread and emit its result."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # noqa: D401
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.succeeded.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class BatchWorker(QThread):
    """Apply an action to each item, reporting progress and per-item results.

    ``action`` receives one item and returns (success: bool, message: str).
    """

    progress = Signal(int, int, str)        # done, total, message
    item_done = Signal(object, bool, str)   # item, success, message
    finished_all = Signal(int, int)         # success_count, total

    def __init__(self, items: Iterable[Any], action: Callable[[Any], tuple[bool, str]]) -> None:
        super().__init__()
        self._items = list(items)
        self._action = action
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D401
        total = len(self._items)
        success = 0
        for idx, item in enumerate(self._items, start=1):
            if self._cancelled:
                break
            try:
                ok, msg = self._action(item)
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, str(exc)
            if ok:
                success += 1
            self.item_done.emit(item, ok, msg)
            self.progress.emit(idx, total, msg)
        self.finished_all.emit(success, total)
