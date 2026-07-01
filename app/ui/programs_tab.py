"""Installed Programs (Win32 / MSI) uninstall tab.

Complements the Bloatware (AppX) tab by covering the classic *Programs and
Features* list — MSI products such as the Windows SDK, runtimes, and desktop
apps that the Store-package tooling cannot see or remove.
"""

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

from app.core import programs, restore
from app.core.actionlog import KIND_PROGRAM_UNINSTALL, KIND_RESTORE_POINT, Action
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

_GREY = QColor("#8a8f98")


def _name_fg(prog: programs.Program):
    return _GREY if (prog.is_update or prog.is_system_component) else None


def _type_text(prog: programs.Program) -> str:
    if prog.is_update:
        return "Update"
    if prog.is_system_component:
        return "System component"
    return "MSI" if prog.is_msi else "App"


def _type_tip(prog: programs.Program):
    if prog.is_msi and not prog.product_code:
        return "MSI product without a ProductCode; removal falls back to its uninstall string."
    if not prog.is_msi and not prog.quiet_uninstall_string:
        return "No silent uninstall string; the vendor's uninstaller UI may appear."
    return None


def _size_text(prog: programs.Program) -> str:
    return f"{prog.size_mb:g} MB" if prog.size_mb else "-"


def _prog_filter(prog: programs.Program, needle: str) -> bool:
    return (
        needle in prog.name.lower()
        or needle in prog.publisher.lower()
        or needle in prog.version.lower()
    )


class ProgramsTab(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self._programs: list[programs.Program] = []
        self._worker = None
        self._batch = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(
            HeaderBar(
                "Installed Programs",
                "Uninstall classic desktop / MSI programs (e.g. the Windows SDK) that don't "
                "appear in the Bloatware tab. Safe mode hides Windows updates and system "
                "components; enable Advanced mode to show everything.",
            )
        )

        self.search = SearchBar("Search programs by name, publisher, or version...")
        self.search.search_changed.connect(self._on_search)
        self.search.refresh_clicked.connect(lambda: self.reload(force=True))
        layout.addWidget(self.search)

        # Group picker: one-click select multi-part suites (e.g. the Windows SDK).
        group_row = QHBoxLayout()
        group_row.addWidget(QLabel("Group:"))
        self.group_combo = QComboBox()
        self.group_combo.addItem("— select a program group —", userData=None)
        self._groups = programs.load_groups()
        for g in self._groups:
            self.group_combo.addItem(g.name, userData=g.id)
            self.group_combo.setItemData(
                self.group_combo.count() - 1, g.description, Qt.ToolTipRole
            )
        self.group_combo.currentIndexChanged.connect(self._apply_group)
        group_row.addWidget(self.group_combo, 1)
        layout.addLayout(group_row)

        self._columns = [
            Column("", checkable=True, resize="contents"),
            Column("Program", display=lambda p: p.name, foreground=_name_fg, width=300),
            Column("Version", display=lambda p: p.version or "-", resize="contents"),
            Column("Publisher", display=lambda p: p.publisher or "-", width=200),
            Column(
                "Size",
                display=_size_text,
                sort_key=lambda p: p.estimated_kb,
                resize="contents",
            ),
            Column("Type", display=_type_text, tooltip=_type_tip, resize="contents"),
        ]
        self.model = ObjectTableModel(self._columns)
        self.proxy = RowFilterProxy(_prog_filter)
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
        self.remove_btn = QPushButton("Uninstall selected")
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
        if not self._programs:
            self.reload()

    def reload(self, force: bool = False) -> None:
        self.status.setText("Loading installed programs...")
        self.search.refresh_btn.setEnabled(False)
        self._worker = FnWorker(programs.list_programs)
        self._worker.succeeded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_load_failed)
        self._worker.start()

    def _on_loaded(self, items) -> None:
        self._programs = items
        self.search.refresh_btn.setEnabled(True)
        self._populate()
        self.status.setText("")

    def _on_load_failed(self, msg: str) -> None:
        self.search.refresh_btn.setEnabled(True)
        self.status.setText(f"Failed to load: {msg}")

    # ------------------------------------------------------------------
    # Filtering / display
    # ------------------------------------------------------------------

    def _visible_programs(self) -> list[programs.Program]:
        if self.ctx.is_advanced():
            return list(self._programs)
        # Safe mode: hide OS updates and hidden system components.
        return [p for p in self._programs if not p.is_update and not p.is_system_component]

    def _populate(self) -> None:
        self.model.set_objects(self._visible_programs())
        self._update_status()

    def _on_search(self, text: str) -> None:
        self.proxy.setFilterString(text)
        self._update_status()

    def _update_status(self) -> None:
        shown = self.proxy.rowCount()
        self.search.set_count(shown, len(self._programs))
        mode = "Advanced" if self.ctx.is_advanced() else "Safe"
        self.status.setText(f"Mode: {mode} — showing {shown} of {len(self._programs)} program(s)")

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _select_all_shown(self) -> None:
        self.model.set_checked(visible_objects(self.table, self.proxy, self.model), True)

    def _clear_selection(self) -> None:
        self.model.uncheck_all()

    def _apply_group(self, index: int) -> None:
        """Check every visible row that belongs to the chosen program group."""
        group_id = self.group_combo.itemData(index)
        if not group_id:
            return
        group = next((g for g in self._groups if g.id == group_id), None)
        if group is None:
            return
        matched_ids = {id(p) for p in programs.match_group(group, self._programs)}
        shown = visible_objects(self.table, self.proxy, self.model)
        targets = [p for p in shown if id(p) in matched_ids]
        self.model.set_checked(targets, True)
        self.status.setText(f"Group '{group.name}' selected {len(targets)} visible program(s).")

    def _checked_programs(self) -> list[programs.Program]:
        return self.model.checked_objects()

    # ------------------------------------------------------------------
    # Uninstall
    # ------------------------------------------------------------------

    def _remove_selected(self) -> None:
        targets = self._checked_programs()
        if not targets:
            info(self, "Nothing selected", "Tick one or more programs to uninstall.")
            return

        names = "\n".join(f"  - {p.name}" for p in targets[:15])
        more = "" if len(targets) <= 15 else f"\n  ...and {len(targets) - 15} more"
        non_silent = [p for p in targets if not p.is_msi and not p.quiet_uninstall_string]
        extra = ""
        if non_silent:
            extra = (
                f"\n\n[!] {len(non_silent)} program(s) have no silent uninstaller; "
                "their own uninstall window may appear and require clicks."
            )

        if not confirm(
            self,
            "Confirm uninstall",
            f"Uninstall {len(targets)} program(s)?\n\n{names}{more}"
            "\n\nThis is not undoable from the History tab — reinstall from the "
            "vendor if needed." + extra,
            danger=True,
        ):
            return

        if self.ctx.want_restore_point():
            self.remove_btn.setEnabled(False)
            self.status.setText("Creating system restore point…")
            self._rp_worker = FnWorker(
                restore.create_restore_point, "Win Debloater - program uninstall"
            )
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

        self._batch = BatchWorker(targets, self._do_uninstall)
        self._batch.progress.connect(lambda d, t, m: self.progress.setValue(d))
        self._batch.item_done.connect(self._on_item_done)
        self._batch.finished_all.connect(self._on_batch_done)
        self._batch.start()

    def _cancel_batch(self) -> None:
        if self._batch is not None and self._batch.isRunning():
            self._batch.cancel()
            self.status.setText("Cancelling\u2026")

    def _do_uninstall(self, prog: programs.Program) -> tuple[bool, str]:
        res = programs.uninstall_program(prog)
        ok = res.ok
        msg = res.error or res.stdout.strip() or ("Uninstalled" if ok else "Failed")
        self.ctx.log.add(
            Action(
                kind=KIND_PROGRAM_UNINSTALL,
                target=prog.name,
                summary=f"Uninstalled program '{prog.name}' {prog.version}".strip(),
                success=ok,
                undoable=False,
            )
        )
        return ok, msg

    def _on_item_done(self, item, ok: bool, msg: str) -> None:
        self.status.setText(f"{'OK' if ok else 'FAILED'}: {item.name} — {msg}")

    def _on_batch_done(self, success: int, total: int) -> None:
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.remove_btn.setEnabled(True)
        self.ctx.notify_log_changed()
        self.status.setText(f"Done: uninstalled {success} of {total} program(s).")
        self.reload(force=True)

    def _show_context_menu(self, pos: QPoint) -> None:
        prog = object_at_pos(self.table, self.proxy, self.model, pos)
        if not prog:
            return
        menu = QMenu(self.table)
        is_checked = self.model.is_checked(prog)
        menu.addAction(
            "Uncheck" if is_checked else "Check",
            lambda: self.model.set_checked([prog], not is_checked),
        )
        menu.addSeparator()
        menu.addAction(
            "Copy name",
            lambda: QGuiApplication.clipboard().setText(prog.name),
        )
        if prog.product_code:
            menu.addAction(
                f"Copy ProductCode ({prog.product_code})",
                lambda: QGuiApplication.clipboard().setText(prog.product_code),
            )
        if prog.uninstall_string:
            menu.addAction(
                "Copy uninstall string",
                lambda: QGuiApplication.clipboard().setText(prog.uninstall_string),
            )
        menu.exec(self.table.viewport().mapToGlobal(pos))
