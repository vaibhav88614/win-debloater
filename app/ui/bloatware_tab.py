"""Bloatware (AppX) removal tab."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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

# Columns
_COL_CHK   = 0
_COL_NAME  = 1
_COL_CAT   = 2
_COL_FLAGS = 3
_COL_DESC  = 4


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
                "Select apps to uninstall. Safe mode shows common, reinstallable apps. "
                "Enable Advanced mode to also see system apps, Edge, NonRemovable packages, "
                "and third-party OEM software.",
            )
        )

        self.search = SearchBar("Search apps by name or category...")
        self.search.search_changed.connect(self._apply_filter)
        self.search.refresh_clicked.connect(self.reload)
        layout.addWidget(self.search)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "App", "Category", "Flags", "Description"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(_COL_CHK,   QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_COL_NAME,  QHeaderView.Interactive)
        hh.setSectionResizeMode(_COL_CAT,   QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_COL_FLAGS, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_COL_DESC,  QHeaderView.Stretch)
        self.table.setColumnWidth(_COL_NAME, 230)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        self.select_btn = QPushButton("Select all shown")
        self.select_btn.setObjectName("ghost")
        self.select_btn.clicked.connect(self._select_all_shown)
        self.clear_btn = QPushButton("Clear selection")
        self.clear_btn.setObjectName("ghost")
        self.clear_btn.clicked.connect(self._clear_selection)
        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.setObjectName("danger")
        self.remove_btn.clicked.connect(self._remove_selected)

        self.progress = QProgressBar()
        self.progress.setVisible(False)

        controls.addWidget(self.select_btn)
        controls.addWidget(self.clear_btn)
        controls.addStretch(1)
        controls.addWidget(self.progress, 1)
        controls.addWidget(self.remove_btn)
        layout.addLayout(controls)

        self.status = QLabel("")
        self.status.setObjectName("subtitle")
        layout.addWidget(self.status)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Filtering / display
    # ------------------------------------------------------------------

    def _visible_packages(self) -> list[appx.AppxPackage]:
        """Return packages that should be shown given the current mode."""
        advanced = self.ctx.is_advanced()
        if advanced:
            return list(self._packages)
        # Safe mode: show only curated safe entries (no uncataloged, no NonRemovable)
        return [p for p in self._packages if p.safe and not p.is_non_removable]

    def _populate(self) -> None:
        query = self.search.input.text().lower().strip()
        pkgs = self._visible_packages()
        if query:
            pkgs = [
                p for p in pkgs
                if query in p.display_name.lower()
                or query in p.category.lower()
                or query in p.name.lower()
                or query in p.description.lower()
            ]

        self.table.setRowCount(0)
        for pkg in pkgs:
            row = self.table.rowCount()
            self.table.insertRow(row)

            # --- Checkbox ---
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Unchecked)
            chk.setData(Qt.UserRole, pkg)
            self.table.setItem(row, _COL_CHK, chk)

            # --- Name ---
            name_item = QTableWidgetItem(pkg.display_name)
            tip_parts: list[str] = []
            if pkg.removal_note:
                tip_parts.append(pkg.removal_note)
            if not pkg.safe:
                tip_parts.append("Advanced: removing may affect system features.")
            if tip_parts:
                name_item.setToolTip(" | ".join(tip_parts))
            if pkg.is_non_removable:
                name_item.setForeground(QColor("#f2c14e"))  # amber = needs force
            self.table.setItem(row, _COL_NAME, name_item)

            # --- Category ---
            self.table.setItem(row, _COL_CAT, QTableWidgetItem(pkg.category))

            # --- Flags ---
            flags: list[str] = []
            if pkg.is_provisioned:
                flags.append("Provisioned")
            if pkg.is_non_removable:
                flags.append("[!] Force req.")
            elif pkg.full_name:
                flags.append("Installed")
            flag_item = QTableWidgetItem(", ".join(flags) or "-")
            if pkg.is_non_removable:
                flag_item.setToolTip(
                    "Windows marks this package NonRemovable. "
                    "The tool will attempt a registry-unlock removal (requires Administrator). "
                    "This may not work on all builds."
                )
            self.table.setItem(row, _COL_FLAGS, flag_item)

            # --- Description ---
            self.table.setItem(row, _COL_DESC, QTableWidgetItem(pkg.description or pkg.name))

        nr_count = sum(1 for p in pkgs if p.is_non_removable)
        mode = "Advanced" if self.ctx.is_advanced() else "Safe"
        note = f"  ({nr_count} force-removal)" if nr_count else ""
        self.search.set_count(self.table.rowCount(), len(self._packages))
        self.status.setText(f"Mode: {mode} — showing {self.table.rowCount()} of {len(self._packages)} packages{note}")

    def _apply_filter(self) -> None:
        self._populate()

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _select_all_shown(self) -> None:
        for row in range(self.table.rowCount()):
            self.table.item(row, _COL_CHK).setCheckState(Qt.Checked)

    def _clear_selection(self) -> None:
        for row in range(self.table.rowCount()):
            self.table.item(row, _COL_CHK).setCheckState(Qt.Unchecked)

    def _checked_packages(self) -> list[appx.AppxPackage]:
        result = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, _COL_CHK)
            if item.checkState() == Qt.Checked:
                result.append(item.data(Qt.UserRole))
        return result

    # ------------------------------------------------------------------
    # Removal
    # ------------------------------------------------------------------

    def _remove_selected(self) -> None:
        targets = self._checked_packages()
        if not targets:
            info(self, "Nothing selected", "Tick one or more apps to remove.")
            return

        nr_targets = [p for p in targets if p.is_non_removable]
        names = "\n".join(f"  - {p.display_name}" for p in targets[:15])
        more = "" if len(targets) <= 15 else f"\n  ...and {len(targets) - 15} more"

        extra_warn = ""
        if nr_targets:
            extra_warn = (
                f"\n\n[!] {len(nr_targets)} package(s) are marked NonRemovable by Windows.\n"
                "A registry-unlock will be attempted first (requires Administrator)."
            )

        if not confirm(
            self,
            "Confirm removal",
            f"Remove {len(targets)} app(s)?\n\n{names}{more}"
            "\n\nAll removals are logged — you can attempt to restore them via the History tab."
            + extra_warn,
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
        res = appx.remove_package(pkg, all_users=True, deprovision=True)
        ok = res.ok
        msg = res.error or res.stdout.strip() or ("Removed" if ok else "Failed")
        self.ctx.log.add(
            Action(
                kind=KIND_APPX_REMOVE,
                target=pkg.display_name,
                summary=f"Removed AppX '{pkg.name}' (force={pkg.is_non_removable})",
                success=ok,
                undoable=True,
                undo_data={"name": pkg.name, "full_name": pkg.full_name},
            )
        )
        return ok, msg

    def _on_item_done(self, item, ok: bool, msg: str) -> None:
        self.status.setText(f"{'OK' if ok else 'FAILED'}: {item.display_name} — {msg}")

    def _on_batch_done(self, success: int, total: int) -> None:
        self.progress.setVisible(False)
        self.remove_btn.setEnabled(True)
        self.ctx.notify_log_changed()
        self.status.setText(f"Done: removed {success} of {total} app(s).")
        self.reload()

    def _make_restore_point(self) -> None:
        self.status.setText("Creating system restore point…")
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
