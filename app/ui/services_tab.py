"""Background services control tab."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core import services
from app.core.actionlog import Action, KIND_SERVICE_STARTTYPE, KIND_SERVICE_STATE
from app.ui.widgets import HeaderBar, SearchBar, confirm, info
from app.ui.workers import FnWorker


class ServicesTab(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self._services: list[services.ServiceInfo] = []
        self._worker = None

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
        self.search.search_changed.connect(self._populate)
        self.search.refresh_clicked.connect(self.reload)
        layout.addWidget(self.search)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Service", "Status", "Startup", "Name", "Description"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Interactive)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(3, 160)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("ghost")
        self.start_btn.clicked.connect(lambda: self._change_state(True))
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("ghost")
        self.stop_btn.clicked.connect(lambda: self._change_state(False))

        self.starttype = QComboBox()
        self.starttype.addItems(services.START_TYPES)
        self.starttype.setCurrentText("Manual")
        self.apply_btn = QPushButton("Apply startup type")
        self.apply_btn.clicked.connect(self._apply_starttype)

        controls.addWidget(self.start_btn)
        controls.addWidget(self.stop_btn)
        controls.addStretch(1)
        controls.addWidget(QLabel("Startup:"))
        controls.addWidget(self.starttype)
        controls.addWidget(self.apply_btn)
        layout.addLayout(controls)

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
        query = self.search.input.text().lower().strip()
        rows = self._visible()
        if query:
            rows = [
                s for s in rows
                if query in s.display_name.lower() or query in s.name.lower()
            ]

        self.table.setRowCount(0)
        for svc in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            name_item = QTableWidgetItem(svc.display_name)
            name_item.setData(Qt.UserRole, svc)
            if svc.is_protected:
                name_item.setToolTip("Protected system service (read-only).")
            self.table.setItem(r, 0, name_item)
            self.table.setItem(r, 1, QTableWidgetItem(svc.status))
            self.table.setItem(r, 2, QTableWidgetItem(svc.start_type))
            self.table.setItem(r, 3, QTableWidgetItem(svc.name))
            self.table.setItem(r, 4, QTableWidgetItem(svc.description[:160]))
        self.search.set_count(self.table.rowCount(), len(self._services))

    def _selected(self) -> list[services.ServiceInfo]:
        out = []
        for idx in self.table.selectionModel().selectedRows():
            item = self.table.item(idx.row(), 0)
            svc = item.data(Qt.UserRole)
            if svc:
                out.append(svc)
        return out

    def _change_state(self, start: bool) -> None:
        sel = [s for s in self._selected() if not s.is_protected]
        if not sel:
            info(self, "No services", "Select one or more non-protected services.")
            return
        verb = "start" if start else "stop"
        if not confirm(self, f"Confirm {verb}", f"{verb.capitalize()} {len(sel)} service(s)?", danger=not start):
            return
        ok_count = 0
        for svc in sel:
            was_running = svc.status.lower() == "running"
            res = services.start_service(svc.name) if start else services.stop_service(svc.name)
            if res.ok:
                ok_count += 1
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
        self.ctx.notify_log_changed()
        self.status.setText(f"{verb.capitalize()}ed {ok_count} of {len(sel)} service(s).")
        self.reload()

    def _apply_starttype(self) -> None:
        sel = [s for s in self._selected() if not s.is_protected]
        if not sel:
            info(self, "No services", "Select one or more non-protected services.")
            return
        new_type = self.starttype.currentText()
        if not confirm(
            self, "Confirm startup change",
            f"Set startup type of {len(sel)} service(s) to '{new_type}'?",
            danger=new_type == "Disabled",
        ):
            return
        ok_count = 0
        for svc in sel:
            res = services.set_start_type(svc.name, new_type)
            if res.ok:
                ok_count += 1
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
        self.ctx.notify_log_changed()
        self.status.setText(f"Updated {ok_count} of {len(sel)} service(s).")
        self.reload()
