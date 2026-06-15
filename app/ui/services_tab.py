"""Background services control tab."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.core import services
from app.core.actionlog import KIND_SERVICE_STARTTYPE, KIND_SERVICE_STATE, Action
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


def _name_tip(svc: services.ServiceInfo):
    return "Protected system service (read-only)." if svc.is_protected else None


def _svc_filter(svc: services.ServiceInfo, needle: str) -> bool:
    return needle in svc.display_name.lower() or needle in svc.name.lower()


class ServicesTab(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self._services: list[services.ServiceInfo] = []
        self._worker = None
        self._batch = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(
            HeaderBar(
                "Background Services",
                "Stop, start, or change startup type. Safe mode shows common "
                "privacy/performance services. Protected system services are locked.",
            )
        )

        self.search = SearchBar("Search services...")
        self.search.search_changed.connect(self._on_search)
        self.search.refresh_clicked.connect(self.reload)
        layout.addWidget(self.search)

        self._columns = [
            Column("Service", display=lambda s: s.display_name, tooltip=_name_tip, width=240),
            Column("Status", display=lambda s: s.status, resize="contents"),
            Column("Startup", display=lambda s: s.start_type, resize="contents"),
            Column("Name", display=lambda s: s.name, width=160),
            Column("Description", display=lambda s: s.description[:160], resize="stretch"),
        ]
        self.model = ObjectTableModel(self._columns)
        self.proxy = RowFilterProxy(_svc_filter)
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
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("ghost")
        self.start_btn.setIcon(std_icon(self, "SP_MediaPlay"))
        self.start_btn.clicked.connect(lambda: self._change_state(True))
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("ghost")
        self.stop_btn.setIcon(std_icon(self, "SP_MediaStop"))
        self.stop_btn.clicked.connect(lambda: self._change_state(False))

        self.starttype = QComboBox()
        self.starttype.addItems(services.START_TYPES)
        self.starttype.setCurrentText("Manual")
        self.apply_btn = QPushButton("Apply startup type")
        self.apply_btn.clicked.connect(self._apply_starttype)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("ghost")
        self.cancel_btn.setIcon(std_icon(self, "SP_BrowserStop"))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_batch)

        controls.addWidget(self.start_btn)
        controls.addWidget(self.stop_btn)
        controls.addStretch(1)
        controls.addWidget(self.cancel_btn)
        controls.addWidget(QLabel("Startup:"))
        controls.addWidget(self.starttype)
        controls.addWidget(self.apply_btn)
        layout.addLayout(controls)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setObjectName("subtitle")
        layout.addWidget(self.status)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._services:
            self.reload()

    def reload(self) -> None:
        self.status.setText("Loading services...")
        self.search.refresh_btn.setEnabled(False)
        self._worker = FnWorker(services.list_services)
        self._worker.succeeded.connect(self._on_loaded)
        self._worker.failed.connect(lambda m: self.status.setText(f"Failed: {m}"))
        self._worker.start()

    def _on_loaded(self, data) -> None:
        self._services = data
        self.search.refresh_btn.setEnabled(True)
        self._populate()
        self.status.setText("")

    def _visible(self) -> list[services.ServiceInfo]:
        advanced = self.ctx.is_advanced()
        return [s for s in self._services if advanced or s.safe]

    def _populate(self) -> None:
        self.model.set_objects(self._visible())
        self.search.set_count(self.proxy.rowCount(), len(self._services))

    def _on_search(self, text: str) -> None:
        self.proxy.setFilterString(text)
        self.search.set_count(self.proxy.rowCount(), len(self._services))

    def _selected(self) -> list[services.ServiceInfo]:
        return selected_objects(self.table, self.proxy, self.model)

    def _change_state(self, start: bool) -> None:
        sel = [s for s in self._selected() if not s.is_protected]
        if not sel:
            info(self, "No services", "Select one or more non-protected services.")
            return
        verb = "start" if start else "stop"
        if not confirm(
            self, f"Confirm {verb}", f"{verb.capitalize()} {len(sel)} service(s)?", danger=not start
        ):
            return

        def do(svc: services.ServiceInfo) -> tuple[bool, str]:
            was_running = svc.status.lower() == "running"
            res = services.start_service(svc.name) if start else services.stop_service(svc.name)
            self.ctx.log.add(
                Action(
                    kind=KIND_SERVICE_STATE,
                    target=svc.display_name,
                    summary=f"{verb.capitalize()}ed service '{svc.name}'",
                    success=res.ok,
                    undoable=True,
                    undo_data={"name": svc.name, "was_running": was_running},
                )
            )
            return res.ok, (res.error or res.stdout.strip() or verb.capitalize() + "ed")

        self._run_batch(
            sel, do, label=verb.capitalize() + "ed", busy_buttons=(self.start_btn, self.stop_btn)
        )

    def _apply_starttype(self) -> None:
        sel = [s for s in self._selected() if not s.is_protected]
        if not sel:
            info(self, "No services", "Select one or more non-protected services.")
            return
        new_type = self.starttype.currentText()
        if not confirm(
            self,
            "Confirm startup change",
            f"Set startup type of {len(sel)} service(s) to '{new_type}'?",
            danger=new_type == "Disabled",
        ):
            return

        def do(svc: services.ServiceInfo) -> tuple[bool, str]:
            res = services.set_start_type(svc.name, new_type)
            self.ctx.log.add(
                Action(
                    kind=KIND_SERVICE_STARTTYPE,
                    target=svc.display_name,
                    summary=f"Set '{svc.name}' startup to {new_type}",
                    success=res.ok,
                    undoable=True,
                    undo_data={"name": svc.name, "previous_start_type": svc.start_type},
                )
            )
            return res.ok, (res.error or res.stdout.strip() or "Updated")

        self._run_batch(sel, do, label="Updated", busy_buttons=(self.apply_btn,))

    def _run_batch(self, items, action, *, label: str, busy_buttons: tuple) -> None:
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
                f"{'OK' if ok else 'FAILED'}: {it.display_name} — {msg}"
            )
        )

        def done(success: int, total: int) -> None:
            self.progress.setVisible(False)
            self.cancel_btn.setVisible(False)
            for b in busy_buttons:
                b.setEnabled(True)
            self.ctx.notify_log_changed()
            self.status.setText(f"{label} {success} of {total} service(s).")
            self.reload()

        self._batch.finished_all.connect(done)
        self._batch.start()

    def _cancel_batch(self) -> None:
        if self._batch is not None and self._batch.isRunning():
            self._batch.cancel()
            self.status.setText("Cancelling…")

    def _show_context_menu(self, pos: QPoint) -> None:
        svc = object_at_pos(self.table, self.proxy, self.model, pos)
        if not svc:
            return
        menu = QMenu(self.table)
        if not svc.is_protected:
            act_start = QAction("Start", menu)
            act_start.triggered.connect(lambda: self._change_state(True))
            act_stop = QAction("Stop", menu)
            act_stop.triggered.connect(lambda: self._change_state(False))
            menu.addAction(act_start)
            menu.addAction(act_stop)
            menu.addSeparator()
        act_copy = QAction(f"Copy name ({svc.name})", menu)
        act_copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(svc.name))
        menu.addAction(act_copy)
        menu.exec(self.table.viewport().mapToGlobal(pos))
