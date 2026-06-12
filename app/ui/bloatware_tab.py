"""Bloatware (AppX) removal tab."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core import appx
from app.core import restore
from app.core.actionlog import Action, KIND_APPX_REMOVE, KIND_RESTORE_POINT
from app.ui.widgets import HeaderBar, SearchBar, confirm, info, warn
from app.ui.workers import BatchWorker, FnWorker


class BloatwareTab(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self._packages: list[appx.AppxPackage] = []
        self._worker = None
        self._batch = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(
            HeaderBar(
                "Bloatware Removal",
                "Select Store apps to uninstall. Safe mode shows only known, "
                "reinstallable apps. Enable Advanced mode to see everything.",
            )
        )

        self.search = SearchBar("Search apps by name or category...")
        self.search.search_changed.connect(self._apply_filter)
        self.search.refresh_clicked.connect(self.reload)
        layout.addWidget(self.search)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "App", "Category", "Status", "Description"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 220)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        self.select_safe_btn = QPushButton("Select all shown")
        self.select_safe_btn.setObjectName("ghost")
        self.select_safe_btn.clicked.connect(self._select_all_shown)
        self.clear_btn = QPushButton("Clear selection")
        self.clear_btn.setObjectName("ghost")
        self.clear_btn.clicked.connect(self._clear_selection)
        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.setObjectName("danger")
        self.remove_btn.clicked.connect(self._remove_selected)

        self.progress = QProgressBar()
        self.progress.setVisible(False)

        controls.addWidget(self.select_safe_btn)
        controls.addWidget(self.clear_btn)
        controls.addStretch(1)
        controls.addWidget(self.progress, 1)
        controls.addWidget(self.remove_btn)
        layout.addLayout(controls)

        self.status = QLabel("")
        self.status.setObjectName("subtitle")
        layout.addWidget(self.status)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._packages:
            self.reload()

    def reload(self) -> None:
        self.status.setText("Loading installed apps...")
        self.search.refresh_btn.setEnabled(False)
        self._worker = FnWorker(appx.list_installed)
        self._worker.succeeded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_load_failed)
        self._worker.start()

    def _on_loaded(self, packages) -> None:
        self._packages = packages
        self.search.refresh_btn.setEnabled(True)
        self._populate()
        self.status.setText("")

    def _on_load_failed(self, msg: str) -> None:
        self.search.refresh_btn.setEnabled(True)
        self.status.setText(f"Failed to load: {msg}")

    def _visible_packages(self) -> list[appx.AppxPackage]:
        advanced = self.ctx.is_advanced()
        return [p for p in self._packages if advanced or p.safe]

    def _populate(self) -> None:
        query = self.search.input.text().lower().strip()
        pkgs = self._visible_packages()
        if query:
            pkgs = [
                p for p in pkgs
                if query in p.display_name.lower()
                or query in p.category.lower()
                or query in p.name.lower()
            ]

        self.table.setRowCount(0)
        for pkg in pkgs:
            row = self.table.rowCount()
            self.table.insertRow(row)

            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Unchecked)
            chk.setData(Qt.UserRole, pkg)
            self.table.setItem(row, 0, chk)

            name_item = QTableWidgetItem(pkg.display_name)
            if not pkg.safe:
                name_item.setToolTip("Advanced: removing this may affect system features.")
            self.table.setItem(row, 1, name_item)

            self.table.setItem(row, 2, QTableWidgetItem(pkg.category))

            status = []
            if pkg.is_provisioned:
                status.append("Provisioned")
            if pkg.full_name:
                status.append("Installed")
            self.table.setItem(row, 3, QTableWidgetItem(", ".join(status) or "-"))

            desc = pkg.description or pkg.name
            self.table.setItem(row, 4, QTableWidgetItem(desc))

        self.search.set_count(self.table.rowCount(), len(self._packages))

    def _apply_filter(self) -> None:
        self._populate()

    def _select_all_shown(self) -> None:
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.Checked)

    def _clear_selection(self) -> None:
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.Unchecked)

    def _checked_packages(self) -> list[appx.AppxPackage]:
        result = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item.checkState() == Qt.Checked:
                result.append(item.data(Qt.UserRole))
        return result

    def _remove_selected(self) -> None:
        targets = self._checked_packages()
        if not targets:
            info(self, "Nothing selected", "Select one or more apps to remove.")
            return

        names = "\n".join(f"  - {p.display_name}" for p in targets[:15])
        more = "" if len(targets) <= 15 else f"\n  ...and {len(targets) - 15} more"
        if not confirm(
            self,
            "Confirm removal",
            f"Remove {len(targets)} app(s)?\n\n{names}{more}\n\n"
            "Removed apps are logged so you can attempt to restore them later.",
            danger=True,
        ):
            return

        if self.ctx.want_restore_point():
            self._make_restore_point()

        self.remove_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(targets))
        self.progress.setValue(0)

        self._batch = BatchWorker(targets, self._do_remove)
        self._batch.progress.connect(lambda d, t, m: self.progress.setValue(d))
        self._batch.item_done.connect(self._on_item_done)
        self._batch.finished_all.connect(self._on_batch_done)
        self._batch.start()

    def _do_remove(self, pkg: appx.AppxPackage) -> tuple[bool, str]:
        res = appx.remove_package(pkg, all_users=self.ctx.is_advanced())
        ok = res.ok
        msg = res.error or res.stdout.strip() or ("Removed" if ok else "Failed")
        self.ctx.log.add(
            Action(
                kind=KIND_APPX_REMOVE,
                target=pkg.display_name,
                summary=f"Removed AppX package '{pkg.name}'",
                success=ok,
                undoable=True,
                undo_data={"name": pkg.name, "full_name": pkg.full_name},
            )
        )
        return ok, msg

    def _on_item_done(self, item, ok: bool, msg: str) -> None:
        self.status.setText(f"{'OK' if ok else 'FAILED'}: {item.display_name} - {msg}")

    def _on_batch_done(self, success: int, total: int) -> None:
        self.progress.setVisible(False)
        self.remove_btn.setEnabled(True)
        self.ctx.notify_log_changed()
        self.status.setText(f"Removed {success} of {total} app(s).")
        self.reload()

    def _make_restore_point(self) -> None:
        self.status.setText("Creating system restore point...")
        res = restore.create_restore_point("Win Debloater - app removal")
        self.ctx.log.add(
            Action(
                kind=KIND_RESTORE_POINT,
                target="System",
                summary="Created system restore point",
                success=res.ok,
            )
        )
        if not res.ok:
            warn(self, "Restore point", res.error or "Could not create a restore point.")
