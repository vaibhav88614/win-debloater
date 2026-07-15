"""QThread workers so backend calls never block the UI."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

from PySide6.QtCore import QThread, Signal

from app.core.applog import get_logger


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

    progress = Signal(int, int, str)  # done, total, message
    item_done = Signal(object, bool, str)  # item, success, message
    finished_all = Signal(int, int)  # success_count, total

    def __init__(self, items: Iterable[Any], action: Callable[[Any], tuple[bool, str]]) -> None:
        super().__init__()
        self._items = list(items)
        self._action = action
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        # Kill any PowerShell child currently running inside the active item so
        # a long removal/change aborts promptly instead of after it completes.
        try:
            from app.core import powershell as ps

            ps.cancel_active()
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> None:  # noqa: D401
        log = get_logger()
        total = len(self._items)
        success = 0
        log.info("BatchWorker: starting %d item(s)", total)
        batch_started = time.monotonic()
        for idx, item in enumerate(self._items, start=1):
            if self._cancelled:
                log.info("BatchWorker: cancelled at item %d/%d", idx, total)
                break
            # Use whatever readable identifier the item exposes.
            label = getattr(item, "display_name", None) or getattr(item, "name", None) or repr(item)
            log.info("BatchWorker: [%d/%d] starting: %s", idx, total, label)
            item_started = time.monotonic()
            try:
                ok, msg = self._action(item)
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, str(exc)
                log.exception("BatchWorker: [%d/%d] '%s' raised", idx, total, label)
            elapsed = int(time.monotonic() - item_started)
            log.info(
                "BatchWorker: [%d/%d] %s: %s (%ss) — %s",
                idx,
                total,
                "OK" if ok else "FAIL",
                label,
                elapsed,
                (msg or "").splitlines()[0] if msg else "",
            )
            if ok:
                success += 1
            self.item_done.emit(item, ok, msg)
            self.progress.emit(idx, total, msg)
        log.info(
            "BatchWorker: finished %d/%d in %ss",
            success,
            total,
            int(time.monotonic() - batch_started),
        )
        self.finished_all.emit(success, total)
