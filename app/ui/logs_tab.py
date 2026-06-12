"""Action history and undo tab."""
from __future__ import annotations

from PySide6.QtGui import QColor
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

from app.core import actionlog
from app.ui.widgets import HeaderBar, confirm, info


class LogsTab(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(
            HeaderBar(
                "Action History & Undo",
                "Every change is recorded here. Reversible actions can be undone "
                "(app reinstall, service/task restore, process resume).",
            )
        )

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Time", "Type", "Target", "Summary", "Result", "Undoable"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(2, 240)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        self.undo_btn = QPushButton("Undo selected")
        self.undo_btn.clicked.connect(self._undo_selected)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("ghost")
        self.refresh_btn.clicked.connect(self.reload)
        self.clear_btn = QPushButton("Clear history")
        self.clear_btn.setObjectName("ghost")
        self.clear_btn.clicked.connect(self._clear)
        controls.addWidget(self.undo_btn)
        controls.addStretch(1)
        controls.addWidget(self.refresh_btn)
        controls.addWidget(self.clear_btn)
        layout.addLayout(controls)

        self.status = QLabel("")
        self.status.setObjectName("subtitle")
        layout.addWidget(self.status)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.reload()

    def reload(self) -> None:
        actions = self.ctx.log.all()
        self.table.setRowCount(0)
        for a in actions:
            r = self.table.rowCount()
            self.table.insertRow(r)
            time_item = QTableWidgetItem(a.time_str)
            time_item.setData(Qt.UserRole, a)
            self.table.setItem(r, 0, time_item)
            self.table.setItem(r, 1, QTableWidgetItem(a.kind))
            self.table.setItem(r, 2, QTableWidgetItem(a.target))
            self.table.setItem(r, 3, QTableWidgetItem(a.summary))
            result = "OK" if a.success else "FAILED"
            res_item = QTableWidgetItem(result)
            res_item.setForeground(QColor("#5fd38a") if a.success else QColor("#f2574d"))
            self.table.setItem(r, 4, res_item)
            if a.undone:
                undo_text = "Undone"
            elif a.undoable and a.success:
                undo_text = "Yes"
            else:
                undo_text = "-"
            self.table.setItem(r, 5, QTableWidgetItem(undo_text))
        self.status.setText(f"{len(actions)} recorded action(s).")

    def _selected_actions(self):
        out = []
        for idx in self.table.selectionModel().selectedRows():
            a = self.table.item(idx.row(), 0).data(Qt.UserRole)
            if a:
                out.append(a)
        return out

    def _undo_selected(self) -> None:
        sel = [a for a in self._selected_actions() if a.undoable and a.success and not a.undone]
        if not sel:
            info(self, "Nothing to undo", "Select one or more undoable actions.")
            return
        if not confirm(self, "Confirm undo", f"Attempt to undo {len(sel)} action(s)?"):
            return
        done = 0
        for a in sel:
            ok, msg = actionlog.perform_undo(a)
            if ok:
                self.ctx.log.mark_undone(a)
                done += 1
        self.reload()
        self.status.setText(f"Undid {done} of {len(sel)} action(s).")

    def _clear(self) -> None:
        if not confirm(self, "Clear history", "Remove all recorded history? This cannot be undone."):
            return
        self.ctx.log.clear()
        self.reload()
