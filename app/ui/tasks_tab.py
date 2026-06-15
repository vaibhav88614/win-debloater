"""Scheduled tasks control tab."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.core import scheduled_tasks as tasks
from app.core.actionlog import KIND_TASK_TOGGLE, Action
from app.ui.models import (
    Column,
    ObjectTableModel,
    RowFilterProxy,
    apply_columns,
    enable_column_menu,
    object_at_pos,
    selected_objects,
)
from app.ui.widgets import HeaderBar, SearchBar, confirm, info, std_icon
from app.ui.workers import BatchWorker, FnWorker


def _task_tip(t: tasks.TaskInfo):
    return "Known telemetry/diagnostic task." if t.is_telemetry else None


def _task_filter(t: tasks.TaskInfo, needle: str) -> bool:
    return needle in t.full_path.lower()


class TasksTab(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self._tasks: list[tasks.TaskInfo] = []
        self._worker = None
        self._batch = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(
            HeaderBar(
                "Scheduled Tasks",
                "Enable or disable scheduled tasks. Safe mode shows known "
                "telemetry/diagnostic tasks. Disabling is fully reversible.",
            )
        )

        self.search = SearchBar("Search tasks...")
        self.search.search_changed.connect(self._on_search)
        self.search.refresh_clicked.connect(self.reload)
        layout.addWidget(self.search)

        self._columns = [
            Column("Task", display=lambda t: t.full_path, tooltip=_task_tip, width=360),
            Column("State", display=lambda t: t.state, resize="contents"),
            Column("Author", display=lambda t: t.author, width=160),
            Column("Description", display=lambda t: t.description[:160], resize="stretch"),
        ]
        self.model = ObjectTableModel(self._columns)
        self.proxy = RowFilterProxy(_task_filter)
        self.proxy.setSourceModel(self.model)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        apply_columns(self.table, self._columns)
        enable_column_menu(self.table, self._columns)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        self.enable_btn = QPushButton("Enable selected")
        self.enable_btn.setObjectName("ghost")
        self.enable_btn.setIcon(std_icon(self, "SP_DialogYesButton"))
        self.enable_btn.clicked.connect(lambda: self._toggle(True))
        self.disable_btn = QPushButton("Disable selected")
        self.disable_btn.setObjectName("danger")
        self.disable_btn.setIcon(std_icon(self, "SP_DialogNoButton"))
        self.disable_btn.clicked.connect(lambda: self._toggle(False))
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("ghost")
        self.cancel_btn.setIcon(std_icon(self, "SP_BrowserStop"))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_batch)
        controls.addStretch(1)
        controls.addWidget(self.cancel_btn)
        controls.addWidget(self.enable_btn)
        controls.addWidget(self.disable_btn)
        layout.addLayout(controls)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setObjectName("subtitle")
        layout.addWidget(self.status)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._tasks:
            self.reload()

    def reload(self) -> None:
        self.status.setText("Loading scheduled tasks...")
        self.search.refresh_btn.setEnabled(False)
        self._worker = FnWorker(tasks.list_tasks)
        self._worker.succeeded.connect(self._on_loaded)
        self._worker.failed.connect(lambda m: self.status.setText(f"Failed: {m}"))
        self._worker.start()

    def _on_loaded(self, data) -> None:
        self._tasks = data
        self.search.refresh_btn.setEnabled(True)
        self._populate()
        self.status.setText("")

    def _visible(self) -> list[tasks.TaskInfo]:
        advanced = self.ctx.is_advanced()
        return [t for t in self._tasks if advanced or t.safe]

    def _populate(self) -> None:
        self.model.set_objects(self._visible())
        self.search.set_count(self.proxy.rowCount(), len(self._tasks))

    def _on_search(self, text: str) -> None:
        self.proxy.setFilterString(text)
        self.search.set_count(self.proxy.rowCount(), len(self._tasks))

    def _selected(self) -> list[tasks.TaskInfo]:
        return selected_objects(self.table, self.proxy, self.model)

    def _toggle(self, enable: bool) -> None:
        sel = self._selected()
        if not sel:
            info(self, "No tasks", "Select one or more tasks.")
            return
        verb = "enable" if enable else "disable"
        if not confirm(
            self, f"Confirm {verb}", f"{verb.capitalize()} {len(sel)} task(s)?", danger=not enable
        ):
            return

        def do(t: tasks.TaskInfo) -> tuple[bool, str]:
            res = tasks.set_enabled(t.path, t.name, enable)
            self.ctx.log.add(
                Action(
                    kind=KIND_TASK_TOGGLE,
                    target=t.full_path,
                    summary=f"{verb.capitalize()}d scheduled task",
                    success=res.ok,
                    undoable=True,
                    undo_data={"path": t.path, "name": t.name, "was_enabled": t.enabled},
                )
            )
            return res.ok, (res.error or res.stdout.strip() or verb.capitalize() + "d")

        self.enable_btn.setEnabled(False)
        self.disable_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(sel))
        self.progress.setValue(0)

        self._batch = BatchWorker(sel, do)
        self._batch.progress.connect(lambda d, t, m: self.progress.setValue(d))
        self._batch.item_done.connect(
            lambda it, ok, msg: self.status.setText(
                f"{'OK' if ok else 'FAILED'}: {it.full_path} — {msg}"
            )
        )

        def done(success: int, total: int) -> None:
            self.progress.setVisible(False)
            self.cancel_btn.setVisible(False)
            self.enable_btn.setEnabled(True)
            self.disable_btn.setEnabled(True)
            self.ctx.notify_log_changed()
            self.status.setText(f"{verb.capitalize()}d {success} of {total} task(s).")
            self.reload()

        self._batch.finished_all.connect(done)
        self._batch.start()

    def _cancel_batch(self) -> None:
        if self._batch is not None and self._batch.isRunning():
            self._batch.cancel()
            self.status.setText("Cancelling\u2026")

    def _show_context_menu(self, pos: QPoint) -> None:
        task = object_at_pos(self.table, self.proxy, self.model, pos)
        if not task:
            return
        menu = QMenu(self.table)
        act_enable = QAction("Enable", menu)
        act_enable.triggered.connect(lambda: self._toggle(True))
        act_disable = QAction("Disable", menu)
        act_disable.triggered.connect(lambda: self._toggle(False))
        menu.addAction(act_enable)
        menu.addAction(act_disable)
        menu.addSeparator()
        act_copy = QAction("Copy path", menu)
        act_copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(task.full_path))
        menu.addAction(act_copy)
        menu.exec(self.table.viewport().mapToGlobal(pos))
