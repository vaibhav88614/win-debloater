"""Action history and undo tab."""

from __future__ import annotations

import csv
import json
import os

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.core import actionlog, applog
from app.ui.models import (
    Column,
    ObjectTableModel,
    RowFilterProxy,
    apply_columns,
    enable_column_menu,
    selected_objects,
)
from app.ui.widgets import HeaderBar, confirm, info, std_icon

_GREEN = QColor("#5fd38a")
_RED = QColor("#f2574d")


def _result_fg(a: actionlog.Action):
    return _GREEN if a.success else _RED


def _undo_text(a: actionlog.Action) -> str:
    if a.undone:
        return "Undone"
    if a.undoable and a.success:
        return "Yes"
    return "-"


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

        # Filter row: by kind, by result, by undone-state.
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Kind:"))
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("All", userData=None)
        self.kind_filter.currentIndexChanged.connect(self.reload)
        filters.addWidget(self.kind_filter)

        filters.addWidget(QLabel("Result:"))
        self.result_filter = QComboBox()
        self.result_filter.addItem("All", userData=None)
        self.result_filter.addItem("Successful", userData=True)
        self.result_filter.addItem("Failed", userData=False)
        self.result_filter.currentIndexChanged.connect(self.reload)
        filters.addWidget(self.result_filter)

        self.hide_undone = QCheckBox("Hide undone")
        self.hide_undone.stateChanged.connect(self.reload)
        filters.addWidget(self.hide_undone)
        filters.addStretch(1)
        layout.addLayout(filters)

        self._columns = [
            Column(
                "Time",
                display=lambda a: a.time_str,
                sort_key=lambda a: a.timestamp,
                resize="contents",
            ),
            Column("Type", display=lambda a: a.kind, resize="contents"),
            Column("Target", display=lambda a: a.target, width=240),
            Column("Summary", display=lambda a: a.summary, resize="stretch"),
            Column(
                "Result",
                display=lambda a: "OK" if a.success else "FAILED",
                sort_key=lambda a: a.success,
                foreground=_result_fg,
                resize="contents",
            ),
            Column("Undoable", display=_undo_text, resize="contents"),
        ]
        self.model = ObjectTableModel(self._columns)
        self.proxy = RowFilterProxy()  # no text filter; sorting only
        self.proxy.setSourceModel(self.model)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        apply_columns(self.table, self._columns)
        enable_column_menu(self.table, self._columns)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        self.undo_btn = QPushButton("Undo selected")
        self.undo_btn.setIcon(std_icon(self, "SP_ArrowBack"))
        self.undo_btn.clicked.connect(self._undo_selected)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("ghost")
        self.refresh_btn.setIcon(std_icon(self, "SP_BrowserReload"))
        self.refresh_btn.clicked.connect(self.reload)
        self.export_btn = QPushButton("Export history\u2026")
        self.export_btn.setObjectName("ghost")
        self.export_btn.setIcon(std_icon(self, "SP_DialogSaveButton"))
        self.export_btn.clicked.connect(self._export_history)
        self.open_log_btn = QPushButton("Open log file")
        self.open_log_btn.setObjectName("ghost")
        self.open_log_btn.setIcon(std_icon(self, "SP_FileIcon"))
        self.open_log_btn.clicked.connect(self._open_log_file)
        self.clear_btn = QPushButton("Clear history")
        self.clear_btn.setObjectName("ghost")
        self.clear_btn.setIcon(std_icon(self, "SP_TrashIcon"))
        self.clear_btn.clicked.connect(self._clear)
        controls.addWidget(self.undo_btn)
        controls.addStretch(1)
        controls.addWidget(self.export_btn)
        controls.addWidget(self.open_log_btn)
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
        all_actions = self.ctx.log.all()
        self._refresh_kind_filter(all_actions)
        actions = self._apply_filters(all_actions)
        self.model.set_objects(actions)
        if len(actions) == len(all_actions):
            self.status.setText(f"{len(all_actions)} recorded action(s).")
        else:
            self.status.setText(
                f"Showing {len(actions)} of {len(all_actions)} action(s) (filters applied)."
            )

    def _refresh_kind_filter(self, actions) -> None:
        """Repopulate the kind dropdown without dropping the user's selection."""
        current = self.kind_filter.currentData()
        kinds = sorted({a.kind for a in actions})
        self.kind_filter.blockSignals(True)
        self.kind_filter.clear()
        self.kind_filter.addItem("All", userData=None)
        for k in kinds:
            self.kind_filter.addItem(k, userData=k)
        if current:
            idx = self.kind_filter.findData(current)
            self.kind_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.kind_filter.blockSignals(False)

    def _apply_filters(self, actions):
        kind = self.kind_filter.currentData()
        result = self.result_filter.currentData()
        hide_undone = self.hide_undone.isChecked()
        out = []
        for a in actions:
            if kind and a.kind != kind:
                continue
            if result is True and not a.success:
                continue
            if result is False and a.success:
                continue
            if hide_undone and a.undone:
                continue
            out.append(a)
        return out

    def _selected_actions(self):
        return selected_objects(self.table, self.proxy, self.model)

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
        if not confirm(
            self, "Clear history", "Remove all recorded history? This cannot be undone."
        ):
            return
        self.ctx.log.clear()
        self.reload()

    def _open_log_file(self) -> None:
        path = applog.log_file_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
        except OSError as exc:
            info(self, "Log file", f"Could not access log file:\n{exc}")
            return
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            # Non-Windows / no associated app — show the path instead.
            info(self, "Log file", str(path))

    def _export_history(self) -> None:
        actions = self._apply_filters(self.ctx.log.all())
        if not actions:
            info(self, "Nothing to export", "No actions match the current filters.")
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export action history",
            "windebloater-history.json",
            "JSON files (*.json);;CSV files (*.csv)",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".csv") or "CSV" in (selected_filter or ""):
                self._write_csv(path, actions)
            else:
                self._write_json(path, actions)
        except OSError as exc:
            info(self, "Export failed", str(exc))
            return
        self.status.setText(f"Exported {len(actions)} action(s) to {path}")

    @staticmethod
    def _write_json(path: str, actions) -> None:
        data = [a.to_dict() for a in actions]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)

    @staticmethod
    def _write_csv(path: str, actions) -> None:
        fieldnames = ["time", "kind", "target", "summary", "success", "undoable", "undone"]
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for a in actions:
                writer.writerow(
                    {
                        "time": a.time_str,
                        "kind": a.kind,
                        "target": a.target,
                        "summary": a.summary,
                        "success": a.success,
                        "undoable": a.undoable,
                        "undone": a.undone,
                    }
                )
