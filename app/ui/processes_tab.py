"""Running processes and suspicious-task detection tab."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.core import processes, suspicious
from app.core.actionlog import KIND_PROCESS_KILL, KIND_PROCESS_SUSPEND, Action
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

_RED = QColor("#f2574d")
_AMBER = QColor("#f2c14e")


def _scan() -> list:
    procs = processes.collect_processes()
    return suspicious.analyze(procs, verify_signatures=True)


def _risk_fg(p: processes.ProcessInfo):
    if p.suspicion_score >= 60:
        return _RED
    if p.suspicion_score >= 40:
        return _AMBER
    return None


def _name_tip(p: processes.ProcessInfo):
    return "Protected system process." if p.is_protected else None


def _detail(p: processes.ProcessInfo) -> str:
    return "; ".join(p.reasons) if p.reasons else (p.exe or p.cmdline)


def _detail_tip(p: processes.ProcessInfo):
    return p.exe or None


def _proc_filter(p: processes.ProcessInfo, needle: str) -> bool:
    return needle in p.name.lower() or needle in p.exe.lower()


class ProcessesTab(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self._procs: list[processes.ProcessInfo] = []
        self._worker = None
        self._batch = None

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
        self.search.search_changed.connect(self._on_search)
        self.search.refresh_clicked.connect(self.reload)
        layout.addWidget(self.search)

        opts = QHBoxLayout()
        self.suspicious_only = QCheckBox("Show suspicious only")
        self.suspicious_only.stateChanged.connect(self._populate)
        opts.addWidget(self.suspicious_only)
        opts.addStretch(1)
        layout.addLayout(opts)

        self._columns = [
            Column(
                "Risk",
                display=lambda p: str(p.suspicion_score),
                sort_key=lambda p: p.suspicion_score,
                foreground=_risk_fg,
                resize="contents",
            ),
            Column("Name", display=lambda p: p.name, tooltip=_name_tip, width=200),
            Column(
                "PID", display=lambda p: str(p.pid), sort_key=lambda p: p.pid, resize="contents"
            ),
            Column(
                "CPU%",
                display=lambda p: f"{p.cpu_percent:.1f}",
                sort_key=lambda p: p.cpu_percent,
                resize="contents",
            ),
            Column(
                "Mem MB",
                display=lambda p: f"{p.memory_mb:.0f}",
                sort_key=lambda p: p.memory_mb,
                resize="contents",
            ),
            Column("User", display=lambda p: p.username, width=140),
            Column(
                "Net",
                display=lambda p: str(p.num_connections),
                sort_key=lambda p: p.num_connections,
                resize="contents",
            ),
            Column("Path / Reasons", display=_detail, tooltip=_detail_tip, resize="stretch"),
        ]
        self.model = ObjectTableModel(self._columns)
        self.proxy = RowFilterProxy(_proc_filter)
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
        self.suspend_btn = QPushButton("Suspend")
        self.suspend_btn.setObjectName("ghost")
        self.suspend_btn.setIcon(std_icon(self, "SP_MediaPause"))
        self.suspend_btn.clicked.connect(self._suspend)
        self.resume_btn = QPushButton("Resume")
        self.resume_btn.setObjectName("ghost")
        self.resume_btn.setIcon(std_icon(self, "SP_MediaPlay"))
        self.resume_btn.clicked.connect(self._resume)
        self.location_btn = QPushButton("Open file location")
        self.location_btn.setObjectName("ghost")
        self.location_btn.setIcon(std_icon(self, "SP_DirOpenIcon"))
        self.location_btn.clicked.connect(self._open_location)
        self.kill_btn = QPushButton("End process")
        self.kill_btn.setObjectName("danger")
        self.kill_btn.setIcon(std_icon(self, "SP_DialogCancelButton"))
        self.kill_btn.clicked.connect(self._kill)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("ghost")
        self.cancel_btn.setIcon(std_icon(self, "SP_BrowserStop"))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_batch)
        controls.addWidget(self.suspend_btn)
        controls.addWidget(self.resume_btn)
        controls.addWidget(self.location_btn)
        controls.addStretch(1)
        controls.addWidget(self.cancel_btn)
        controls.addWidget(self.kill_btn)
        layout.addLayout(controls)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

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

    def _base_rows(self) -> list[processes.ProcessInfo]:
        if self.suspicious_only.isChecked():
            return [p for p in self._procs if p.is_suspicious]
        return list(self._procs)

    def _populate(self) -> None:
        self.model.set_objects(self._base_rows())
        self.search.set_count(self.proxy.rowCount(), len(self._procs))

    def _on_search(self, text: str) -> None:
        self.proxy.setFilterString(text)
        self.search.set_count(self.proxy.rowCount(), len(self._procs))

    def _selected(self) -> list[processes.ProcessInfo]:
        return selected_objects(self.table, self.proxy, self.model)

    def _suspend(self) -> None:
        sel = [p for p in self._selected() if not p.is_protected]
        if not sel:
            info(self, "No process", "Select one or more non-protected processes.")
            return

        def do(p: processes.ProcessInfo) -> tuple[bool, str]:
            ok, msg = processes.suspend_process(p.pid)
            self.ctx.log.add(
                Action(
                    kind=KIND_PROCESS_SUSPEND,
                    target=f"{p.name} (PID {p.pid})",
                    summary="Suspended process",
                    success=ok,
                    undoable=ok,
                    undo_data={"pid": p.pid},
                )
            )
            return ok, msg

        self._run_batch(sel, do, label="Suspended", busy_buttons=(self.suspend_btn,))

    def _resume(self) -> None:
        sel = self._selected()
        if not sel:
            info(self, "No process", "Select one or more processes.")
            return

        def do(p: processes.ProcessInfo) -> tuple[bool, str]:
            return processes.resume_process(p.pid)

        self._run_batch(
            sel,
            do,
            label="Resumed",
            busy_buttons=(self.resume_btn,),
            notify_log=False,
            reload_after=False,
        )

    def _kill(self) -> None:
        sel = [p for p in self._selected() if not p.is_protected]
        if not sel:
            info(self, "No process", "Select one or more non-protected processes.")
            return
        names = "\n".join(f"  - {p.name} (PID {p.pid})" for p in sel[:15])
        if not confirm(self, "End process", f"End {len(sel)} process(es)?\n\n{names}", danger=True):
            return

        def do(p: processes.ProcessInfo) -> tuple[bool, str]:
            ok, msg = processes.kill_process(p.pid)
            self.ctx.log.add(
                Action(
                    kind=KIND_PROCESS_KILL,
                    target=f"{p.name} (PID {p.pid})",
                    summary="Ended process",
                    success=ok,
                    undoable=False,
                )
            )
            return ok, msg

        self._run_batch(sel, do, label="Ended", busy_buttons=(self.kill_btn,))

    def _open_location(self) -> None:
        sel = self._selected()
        if not sel:
            return
        if not processes.open_file_location(sel[0].exe):
            info(self, "Unavailable", "No accessible file location for this process.")

    def _run_batch(
        self,
        items,
        action,
        *,
        label: str,
        busy_buttons: tuple,
        notify_log: bool = True,
        reload_after: bool = True,
    ) -> None:
        for b in busy_buttons:
            b.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(items))
        self.progress.setValue(0)

        self._batch = BatchWorker(items, action)
        self._batch.progress.connect(lambda d, t, m: self.progress.setValue(d))
        self._batch.item_done.connect(
            lambda it, ok, msg: self.status.setText(
                f"{'OK' if ok else 'FAILED'}: {it.name} (PID {it.pid}) — {msg}"
            )
        )

        def done(success: int, total: int) -> None:
            self.progress.setVisible(False)
            self.cancel_btn.setVisible(False)
            for b in busy_buttons:
                b.setEnabled(True)
            if notify_log:
                self.ctx.notify_log_changed()
            self.status.setText(f"{label} {success} of {total} process(es).")
            if reload_after:
                self.reload()

        self._batch.finished_all.connect(done)
        self._batch.start()

    def _cancel_batch(self) -> None:
        if self._batch is not None and self._batch.isRunning():
            self._batch.cancel()
            self.status.setText("Cancelling\u2026")

    def _show_context_menu(self, pos: QPoint) -> None:
        p = object_at_pos(self.table, self.proxy, self.model, pos)
        if not p:
            return
        menu = QMenu(self.table)
        if not p.is_protected:
            menu.addAction("Suspend", self._suspend)
            menu.addAction("Resume", self._resume)
            menu.addSeparator()
            menu.addAction("End process", self._kill)
            menu.addSeparator()
        menu.addAction("Open file location", self._open_location)
        menu.addSeparator()
        menu.addAction(
            f"Copy name ({p.name})",
            lambda: QGuiApplication.clipboard().setText(p.name),
        )
        if p.exe:
            menu.addAction(
                "Copy path",
                lambda: QGuiApplication.clipboard().setText(p.exe),
            )
        menu.exec(self.table.viewport().mapToGlobal(pos))
