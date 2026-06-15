"""Bloatware (AppX) removal tab."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QGuiApplication
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

from app.core import appx, presets, restore
from app.core.actionlog import KIND_APPX_REMOVE, KIND_RESTORE_POINT, Action
from app.ui.models import (
    Column,
    ObjectTableModel,
    RowFilterProxy,
    apply_columns,
    enable_column_menu,
    object_at_pos,
    visible_objects,
)
from app.ui.widgets import HeaderBar, SearchBar, confirm, info, std_icon, warn
from app.ui.workers import BatchWorker, FnWorker

_AMBER = QColor("#f2c14e")


def _name_fg(pkg: appx.AppxPackage):
    return _AMBER if pkg.is_non_removable else None


def _name_tip(pkg: appx.AppxPackage):
    parts: list[str] = []
    if pkg.removal_note:
        parts.append(pkg.removal_note)
    if not pkg.safe:
        parts.append("Advanced: removing may affect system features.")
    return " | ".join(parts) or None


def _flags_text(pkg: appx.AppxPackage) -> str:
    flags: list[str] = []
    if pkg.is_provisioned:
        flags.append("Provisioned")
    if pkg.is_non_removable:
        flags.append("[!] Force req.")
    elif pkg.full_name:
        flags.append("Installed")
    return ", ".join(flags) or "-"


def _flags_tip(pkg: appx.AppxPackage):
    if pkg.is_non_removable:
        return (
            "Windows marks this package NonRemovable. "
            "The tool will attempt a registry-unlock removal (requires Administrator). "
            "This may not work on all builds."
        )
    return None


def _pkg_filter(pkg: appx.AppxPackage, needle: str) -> bool:
    return (
        needle in pkg.display_name.lower()
        or needle in pkg.category.lower()
        or needle in pkg.name.lower()
        or needle in pkg.description.lower()
    )


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
        self.search.search_changed.connect(self._on_search)
        self.search.refresh_clicked.connect(lambda: self.reload(force=True))
        layout.addWidget(self.search)

        # Preset picker: applies a curated selection by category/id.
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("— select a preset —", userData=None)
        self._presets = presets.load_presets()
        for p in self._presets:
            self.preset_combo.addItem(p.name, userData=p.id)
            self.preset_combo.setItemData(
                self.preset_combo.count() - 1, p.description, Qt.ToolTipRole
            )
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        preset_row.addWidget(self.preset_combo, 1)
        layout.addLayout(preset_row)

        # --- Model / view ---
        self._columns = [
            Column("", checkable=True, resize="contents"),
            Column(
                "App",
                display=lambda p: p.display_name,
                foreground=_name_fg,
                tooltip=_name_tip,
                width=230,
            ),
            Column("Category", display=lambda p: p.category, resize="contents"),
            Column("Flags", display=_flags_text, tooltip=_flags_tip, resize="contents"),
            Column("Description", display=lambda p: p.description or p.name, resize="stretch"),
        ]
        self.model = ObjectTableModel(self._columns)
        self.proxy = RowFilterProxy(_pkg_filter)
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
        self.select_btn = QPushButton("Select all shown")
        self.select_btn.setObjectName("ghost")
        self.select_btn.clicked.connect(self._select_all_shown)
        self.clear_btn = QPushButton("Clear selection")
        self.clear_btn.setObjectName("ghost")
        self.clear_btn.clicked.connect(self._clear_selection)
        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.setObjectName("danger")
        self.remove_btn.setIcon(std_icon(self, "SP_TrashIcon"))
        self.remove_btn.clicked.connect(self._remove_selected)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("ghost")
        self.cancel_btn.setIcon(std_icon(self, "SP_BrowserStop"))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_batch)

        self.progress = QProgressBar()
        self.progress.setVisible(False)

        controls.addWidget(self.select_btn)
        controls.addWidget(self.clear_btn)
        controls.addStretch(1)
        controls.addWidget(self.progress, 1)
        controls.addWidget(self.cancel_btn)
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

    def reload(self, force: bool = False) -> None:
        self.status.setText("Loading installed apps...")
        self.search.refresh_btn.setEnabled(False)
        self._worker = FnWorker(appx.list_installed, force=force)
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
        self.model.set_objects(self._visible_packages())
        self._update_status()

    def _on_search(self, text: str) -> None:
        self.proxy.setFilterString(text)
        self._update_status()

    def _update_status(self) -> None:
        shown = self.proxy.rowCount()
        self.search.set_count(shown, len(self._packages))
        nr_count = sum(1 for p in self.model.objects() if p.is_non_removable)
        mode = "Advanced" if self.ctx.is_advanced() else "Safe"
        note = f"  ({nr_count} force-removal)" if nr_count else ""
        self.status.setText(
            f"Mode: {mode} — showing {shown} of {len(self._packages)} packages{note}"
        )

    def _apply_filter(self) -> None:
        self._populate()

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _select_all_shown(self) -> None:
        self.model.set_checked(visible_objects(self.table, self.proxy, self.model), True)

    def _clear_selection(self) -> None:
        self.model.uncheck_all()

    def _apply_preset(self, index: int) -> None:
        """Check every visible row that matches the chosen preset."""
        preset_id = self.preset_combo.itemData(index)
        if not preset_id:
            return
        preset = next((p for p in self._presets if p.id == preset_id), None)
        if preset is None:
            return
        matches = {pkg.name.lower() for pkg in presets.apply_preset(preset, self._packages)}
        shown = visible_objects(self.table, self.proxy, self.model)
        targets = [pkg for pkg in shown if pkg.name.lower() in matches]
        self.model.set_checked(targets, True)
        self.status.setText(f"Preset '{preset.name}' selected {len(targets)} visible package(s).")

    def _checked_packages(self) -> list[appx.AppxPackage]:
        return self.model.checked_objects()

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
            # Create the restore point in a worker, *then* start the batch.
            self.remove_btn.setEnabled(False)
            self.status.setText("Creating system restore point…")
            self._rp_worker = FnWorker(restore.create_restore_point, "Win Debloater - app removal")
            self._rp_worker.succeeded.connect(lambda res: self._after_restore_point(res, targets))
            self._rp_worker.failed.connect(
                lambda msg: self._after_restore_point(None, targets, error=msg)
            )
            self._rp_worker.start()
            return

        self._start_batch(targets)

    def _after_restore_point(self, res, targets, *, error: str | None = None) -> None:
        ok = bool(res and getattr(res, "ok", False))
        self.ctx.log.add(
            Action(
                kind=KIND_RESTORE_POINT,
                target="System",
                summary="Created system restore point",
                success=ok,
            )
        )
        self.ctx.notify_log_changed()
        if not ok:
            msg = (
                error
                or (getattr(res, "error", "") if res else "")
                or "Could not create a restore point."
            )
            warn(self, "Restore point", msg)
        self._start_batch(targets)

    def _start_batch(self, targets) -> None:
        self.remove_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(targets))
        self.progress.setValue(0)

        self._batch = BatchWorker(targets, self._do_remove)
        self._batch.progress.connect(lambda d, t, m: self.progress.setValue(d))
        self._batch.item_done.connect(self._on_item_done)
        self._batch.finished_all.connect(self._on_batch_done)
        self._batch.start()

    def _cancel_batch(self) -> None:
        if self._batch is not None and self._batch.isRunning():
            self._batch.cancel()
            self.status.setText("Cancelling\u2026")

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
        self.cancel_btn.setVisible(False)
        self.remove_btn.setEnabled(True)
        self.ctx.notify_log_changed()
        self.status.setText(f"Done: removed {success} of {total} app(s).")
        self.reload()

    def _show_context_menu(self, pos: QPoint) -> None:
        pkg = object_at_pos(self.table, self.proxy, self.model, pos)
        if not pkg:
            return
        menu = QMenu(self.table)
        is_checked = self.model.is_checked(pkg)
        check_label = "Uncheck" if is_checked else "Check"
        menu.addAction(check_label, lambda: self.model.set_checked([pkg], not is_checked))
        menu.addSeparator()
        menu.addAction(
            f"Copy package name ({pkg.name})",
            lambda: QGuiApplication.clipboard().setText(pkg.name),
        )
        if pkg.full_name:
            menu.addAction(
                "Copy PackageFullName",
                lambda: QGuiApplication.clipboard().setText(pkg.full_name),
            )
        menu.exec(self.table.viewport().mapToGlobal(pos))
