"""Scheduled tasks control tab."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core import scheduled_tasks as tasks
from app.core.actionlog import Action, KIND_TASK_TOGGLE
from app.ui.widgets import HeaderBar, SearchBar, confirm, info
from app.ui.workers import FnWorker


class TasksTab(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self._tasks: list[tasks.TaskInfo] = []
        self._worker = None

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
        self.search.search_changed.connect(self._populate)
        self.search.refresh_clicked.connect(self.reload)
        layout.addWidget(self.search)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Task", "State", "Author", "Description"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 360)
        self.table.setColumnWidth(2, 160)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        self.enable_btn = QPushButton("Enable selected")
        self.enable_btn.setObjectName("ghost")
        self.enable_btn.clicked.connect(lambda: self._toggle(True))
        self.disable_btn = QPushButton("Disable selected")
        self.disable_btn.setObjectName("danger")
        self.disable_btn.clicked.connect(lambda: self._toggle(False))
        controls.addStretch(1)
        controls.addWidget(self.enable_btn)
        controls.addWidget(self.disable_btn)
        layout.addLayout(controls)

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
        query = self.search.input.text().lower().strip()
        rows = self._visible()
        if query:
            rows = [t for t in rows if query in t.full_path.lower()]
        self.table.setRowCount(0)
        for t in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            item = QTableWidgetItem(t.full_path)
            item.setData(Qt.UserRole, t)
            if t.is_telemetry:
                item.setToolTip("Known telemetry/diagnostic task.")
            self.table.setItem(r, 0, item)
            self.table.setItem(r, 1, QTableWidgetItem(t.state))
            self.table.setItem(r, 2, QTableWidgetItem(t.author))
            self.table.setItem(r, 3, QTableWidgetItem(t.description[:160]))
        self.search.set_count(self.table.rowCount(), len(self._tasks))

    def _selected(self) -> list[tasks.TaskInfo]:
        out = []
        for idx in self.table.selectionModel().selectedRows():
            t = self.table.item(idx.row(), 0).data(Qt.UserRole)
            if t:
                out.append(t)
        return out

    def _toggle(self, enable: bool) -> None:
        sel = self._selected()
        if not sel:
            info(self, "No tasks", "Select one or more tasks.")
            return
        verb = "enable" if enable else "disable"
        if not confirm(self, f"Confirm {verb}", f"{verb.capitalize()} {len(sel)} task(s)?", danger=not enable):
            return
        ok_count = 0
        for t in sel:
            res = tasks.set_enabled(t.path, t.name, enable)
            if res.ok:
                ok_count += 1
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
        self.ctx.notify_log_changed()
        self.status.setText(f"{verb.capitalize()}d {ok_count} of {len(sel)} task(s).")
        self.reload()
