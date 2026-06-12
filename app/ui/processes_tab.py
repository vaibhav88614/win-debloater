"""Running processes and suspicious-task detection tab."""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core import processes, suspicious
from app.core.actionlog import Action, KIND_PROCESS_KILL, KIND_PROCESS_SUSPEND
from app.ui.widgets import HeaderBar, SearchBar, confirm, info
from app.ui.workers import FnWorker


def _scan() -> list:
    procs = processes.collect_processes()
    return suspicious.analyze(procs, verify_signatures=True)


class ProcessesTab(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self._procs: list[processes.ProcessInfo] = []
        self._worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(
            HeaderBar(
                "Processes & Suspicious Tasks",
                "Live processes scored by heuristics (unusual paths, unsigned "
                "binaries, impersonation, random names). Suspend, resume, or kill.",
            )
        )

        self.search = SearchBar("Search processes...")
        self.search.search_changed.connect(self._populate)
        self.search.refresh_clicked.connect(self.reload)
        layout.addWidget(self.search)

        opts = QHBoxLayout()
        self.suspicious_only = QCheckBox("Show suspicious only")
        self.suspicious_only.stateChanged.connect(self._populate)
        opts.addWidget(self.suspicious_only)
        opts.addStretch(1)
        layout.addLayout(opts)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Risk", "Name", "PID", "CPU%", "Mem MB", "User", "Net", "Path / Reasons"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        for col in (0, 2, 3, 4, 6):
            hh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(5, QHeaderView.Interactive)
        hh.setSectionResizeMode(7, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(5, 140)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        self.suspend_btn = QPushButton("Suspend")
        self.suspend_btn.setObjectName("ghost")
        self.suspend_btn.clicked.connect(self._suspend)
        self.resume_btn = QPushButton("Resume")
        self.resume_btn.setObjectName("ghost")
        self.resume_btn.clicked.connect(self._resume)
        self.location_btn = QPushButton("Open file location")
        self.location_btn.setObjectName("ghost")
        self.location_btn.clicked.connect(self._open_location)
        self.kill_btn = QPushButton("End process")
        self.kill_btn.setObjectName("danger")
        self.kill_btn.clicked.connect(self._kill)
        controls.addWidget(self.suspend_btn)
        controls.addWidget(self.resume_btn)
        controls.addWidget(self.location_btn)
        controls.addStretch(1)
        controls.addWidget(self.kill_btn)
        layout.addLayout(controls)

        self.status = QLabel("")
        self.status.setObjectName("subtitle")
        layout.addWidget(self.status)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._procs:
            self.reload()

    def reload(self) -> None:
        self.status.setText("Scanning processes...")
        self.search.refresh_btn.setEnabled(False)
        self._worker = FnWorker(_scan)
        self._worker.succeeded.connect(self._on_loaded)
        self._worker.failed.connect(lambda m: self.status.setText(f"Failed: {m}"))
        self._worker.start()

    def _on_loaded(self, data) -> None:
        self._procs = data
        self.search.refresh_btn.setEnabled(True)
        self._populate()
        flagged = sum(1 for p in self._procs if p.is_suspicious)
        self.status.setText(f"{flagged} potentially suspicious process(es) flagged.")

    def _populate(self) -> None:
        query = self.search.input.text().lower().strip()
        rows = self._procs
        if self.suspicious_only.isChecked():
            rows = [p for p in rows if p.is_suspicious]
        if query:
            rows = [p for p in rows if query in p.name.lower() or query in p.exe.lower()]

        self.table.setRowCount(0)
        for p in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)

            risk_item = QTableWidgetItem(str(p.suspicion_score))
            risk_item.setData(Qt.UserRole, p)
            if p.suspicion_score >= 60:
                risk_item.setForeground(QColor("#f2574d"))
            elif p.suspicion_score >= 40:
                risk_item.setForeground(QColor("#f2c14e"))
            self.table.setItem(r, 0, risk_item)

            name_item = QTableWidgetItem(p.name)
            if p.is_protected:
                name_item.setToolTip("Protected system process.")
            self.table.setItem(r, 1, name_item)
            self.table.setItem(r, 2, QTableWidgetItem(str(p.pid)))
            self.table.setItem(r, 3, QTableWidgetItem(f"{p.cpu_percent:.1f}"))
            self.table.setItem(r, 4, QTableWidgetItem(f"{p.memory_mb:.0f}"))
            self.table.setItem(r, 5, QTableWidgetItem(p.username))
            self.table.setItem(r, 6, QTableWidgetItem(str(p.num_connections)))
            detail = "; ".join(p.reasons) if p.reasons else (p.exe or p.cmdline)
            item = QTableWidgetItem(detail)
            if p.exe:
                item.setToolTip(p.exe)
            self.table.setItem(r, 7, item)
        self.search.set_count(self.table.rowCount(), len(self._procs))

    def _selected(self) -> list[processes.ProcessInfo]:
        out = []
        for idx in self.table.selectionModel().selectedRows():
            p = self.table.item(idx.row(), 0).data(Qt.UserRole)
            if p:
                out.append(p)
        return out

    def _suspend(self) -> None:
        sel = [p for p in self._selected() if not p.is_protected]
        if not sel:
            info(self, "No process", "Select one or more non-protected processes.")
            return
        for p in sel:
            ok, msg = processes.suspend_process(p.pid)
            self.ctx.log.add(
                Action(
                    kind=KIND_PROCESS_SUSPEND, target=f"{p.name} (PID {p.pid})",
                    summary="Suspended process", success=ok, undoable=ok,
                    undo_data={"pid": p.pid},
                )
            )
        self.ctx.notify_log_changed()
        self.status.setText(f"Suspended {len(sel)} process(es).")

    def _resume(self) -> None:
        sel = self._selected()
        if not sel:
            info(self, "No process", "Select one or more processes.")
            return
        for p in sel:
            processes.resume_process(p.pid)
        self.status.setText(f"Resumed {len(sel)} process(es).")

    def _kill(self) -> None:
        sel = [p for p in self._selected() if not p.is_protected]
        if not sel:
            info(self, "No process", "Select one or more non-protected processes.")
            return
        names = "\n".join(f"  - {p.name} (PID {p.pid})" for p in sel[:15])
        if not confirm(self, "End process", f"End {len(sel)} process(es)?\n\n{names}", danger=True):
            return
        ok_count = 0
        for p in sel:
            ok, msg = processes.kill_process(p.pid)
            if ok:
                ok_count += 1
            self.ctx.log.add(
                Action(
                    kind=KIND_PROCESS_KILL, target=f"{p.name} (PID {p.pid})",
                    summary="Ended process", success=ok, undoable=False,
                )
            )
        self.ctx.notify_log_changed()
        self.status.setText(f"Ended {ok_count} of {len(sel)} process(es).")
        self.reload()

    def _open_location(self) -> None:
        sel = self._selected()
        if not sel:
            return
        if not processes.open_file_location(sel[0].exe):
            info(self, "Unavailable", "No accessible file location for this process.")
