"""Main application window with tabs and global controls."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import __version__
from app.core import dryrun
from app.core.actionlog import ActionLog
from app.core.elevation import is_admin
from app.ui import theme
from app.ui.bloatware_tab import BloatwareTab
from app.ui.logs_tab import LogsTab
from app.ui.processes_tab import ProcessesTab
from app.ui.programs_tab import ProgramsTab
from app.ui.services_tab import ServicesTab
from app.ui.tasks_tab import TasksTab
from app.ui.workers import FnWorker


class AppContext:
    """Shared state passed to every tab."""

    def __init__(self, window: MainWindow) -> None:
        self._window = window
        self.log = ActionLog()

    def is_advanced(self) -> bool:
        return self._window.advanced_toggle.isChecked()

    def want_restore_point(self) -> bool:
        return self._window.restore_toggle.isChecked()

    def is_dry_run(self) -> bool:
        return self._window.dryrun_toggle.isChecked()

    def notify_log_changed(self) -> None:
        self._window.logs_tab.reload()

    def set_status(self, text: str) -> None:
        self._window.statusBar().showMessage(text, 6000)


class MainWindow(QMainWindow):
    SETTINGS_ORG = "win-debloater"
    SETTINGS_APP = "WinDebloater"

    def __init__(self, is_elevated: bool | None = None) -> None:
        super().__init__()
        self.is_elevated = is_admin() if is_elevated is None else is_elevated
        self.setWindowTitle(f"Windows Debloater & Task Control  v{__version__}")
        self.resize(1100, 720)
        self.setMinimumSize(900, 560)
        self._settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self._theme = theme.normalize(self._settings.value("theme", theme.DEFAULT_THEME))

        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        root.addLayout(self._build_topbar())

        if not self.is_elevated:
            root.addWidget(self._build_admin_banner())

        self.ctx = AppContext(self)

        self.tabs = QTabWidget()
        self.bloatware_tab = BloatwareTab(self.ctx)
        self.programs_tab = ProgramsTab(self.ctx)
        self.services_tab = ServicesTab(self.ctx)
        self.tasks_tab = TasksTab(self.ctx)
        self.processes_tab = ProcessesTab(self.ctx)
        self.logs_tab = LogsTab(self.ctx)
        self.tabs.addTab(self.bloatware_tab, "Bloatware")
        self.tabs.addTab(self.programs_tab, "Installed Programs")
        self.tabs.addTab(self.services_tab, "Services")
        self.tabs.addTab(self.tasks_tab, "Scheduled Tasks")
        self.tabs.addTab(self.processes_tab, "Processes & Suspicious")
        self.tabs.addTab(self.logs_tab, "History")
        root.addWidget(self.tabs, 1)

        # Real QStatusBar replaces the previous title-bar message shim.
        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage(f"Ready — v{__version__}")

        self._build_menu_bar()
        self._apply_style()
        self._install_shortcuts()
        self._install_badges()
        self._restore_settings()
        self._maybe_check_updates_on_start()

    def _maybe_check_updates_on_start(self) -> None:
        """Silently check for updates at launch, only if the user opted in.

        Disabled by default so the app never makes a network request without
        consent (and so the test suite stays offline).
        """
        from app.core import updates

        if updates.DEFAULT_REPO.startswith("OWNER/"):
            return
        if self._auto_update_enabled():
            self._check_for_updates(silent=True)

    def _auto_update_enabled(self) -> bool:
        value = self._settings.value("check_updates_on_start", False)
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "on"}

    def _build_topbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        title = QLabel("Windows Debloater & Task Control")
        title.setObjectName("title")
        bar.addWidget(title)
        bar.addStretch(1)

        self.restore_toggle = QCheckBox("Restore point before changes")
        self.restore_toggle.setChecked(True)
        bar.addWidget(self.restore_toggle)

        self.dryrun_toggle = QCheckBox("Dry run")
        self.dryrun_toggle.setToolTip(
            "Preview-only: actions are logged as '(dry-run)' and no real "
            "changes are made. Useful for testing what a removal would do."
        )
        self.dryrun_toggle.stateChanged.connect(self._on_dryrun_changed)
        bar.addWidget(self.dryrun_toggle)

        self.advanced_toggle = QCheckBox("Advanced mode")
        self.advanced_toggle.setToolTip(
            "Show all packages/services/tasks, including potentially risky items."
        )
        self.advanced_toggle.stateChanged.connect(self._on_mode_changed)
        bar.addSpacing(12)
        bar.addWidget(self.advanced_toggle)

        admin_text = "Administrator" if self.is_elevated else "Limited (not elevated)"
        self.admin_badge = QLabel(f"  {admin_text}  ")
        self.admin_badge.setObjectName("statusbadge")
        bar.addSpacing(12)
        bar.addWidget(self.admin_badge)
        return bar

    def _build_admin_banner(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("banner")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(12, 8, 12, 8)
        text = QLabel(
            "Running without administrator rights. Listing works, but removals, "
            "service/task changes, and restore points may fail. Restart the app "
            "and accept the UAC prompt for full control."
        )
        text.setObjectName("bannerText")
        text.setWordWrap(True)
        lay.addWidget(text)
        return frame

    def _on_mode_changed(self) -> None:
        # Refresh the currently visible data set when the mode changes.
        for tab in (self.bloatware_tab, self.programs_tab, self.services_tab, self.tasks_tab):
            if hasattr(tab, "_populate"):
                tab._populate()
        if self.advanced_toggle.isChecked():
            self.statusBar().showMessage("Advanced mode enabled — extra caution advised.", 6000)
        else:
            self.statusBar().showMessage("Safe mode enabled.", 4000)

    def _on_dryrun_changed(self) -> None:
        enabled = self.dryrun_toggle.isChecked()
        dryrun.set_enabled(enabled)
        if enabled:
            self.statusBar().showMessage("Dry-run enabled — no system changes will be made.", 6000)
        else:
            self.statusBar().showMessage("Dry-run disabled — actions are live.", 4000)

    def _apply_style(self) -> None:
        self.setStyleSheet(theme.build_qss(self._theme))

    def _set_theme(self, name: str) -> None:
        """Switch the active theme, update the menu check, and persist."""
        self._theme = theme.normalize(name)
        self._apply_style()
        act = self._theme_actions.get(self._theme)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        self._settings.setValue("theme", self._theme)

    def _build_menu_bar(self) -> None:
        """Top menu bar with View (theme), Tools, and Help entries."""
        bar: QMenuBar = self.menuBar()

        view_menu = bar.addMenu("&View")
        theme_menu = view_menu.addMenu("&Theme")
        group = QActionGroup(self)
        group.setExclusive(True)
        self._theme_actions: dict[str, QAction] = {}
        for name in theme.THEMES:
            act = QAction(name.capitalize(), self, checkable=True)
            act.setChecked(name == self._theme)
            act.triggered.connect(lambda _checked, n=name: self._set_theme(n))
            group.addAction(act)
            theme_menu.addAction(act)
            self._theme_actions[name] = act

        tools_menu = bar.addMenu("&Tools")
        tools_menu.addAction("Export profile\u2026", self._export_profile)
        tools_menu.addAction("Apply profile\u2026", self._apply_profile)
        tools_menu.addSeparator()
        tools_menu.addAction("Save state snapshot", self._save_snapshot)
        tools_menu.addAction("Show changes since snapshot\u2026", self._show_changes)
        tools_menu.addSeparator()
        tools_menu.addAction("Edit catalog overlay\u2026", self._edit_catalog_overlay)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction("Diagnostics\u2026", self._show_diagnostics)
        help_menu.addAction("Collect support bundle\u2026", self._collect_support_bundle)
        help_menu.addAction(
            "Check for updates\u2026", lambda: self._check_for_updates(silent=False)
        )
        self._auto_update_action = QAction("Check for updates at startup", self, checkable=True)
        self._auto_update_action.setChecked(self._auto_update_enabled())
        self._auto_update_action.toggled.connect(
            lambda on: self._settings.setValue("check_updates_on_start", on)
        )
        help_menu.addAction(self._auto_update_action)
        help_menu.addSeparator()
        about = help_menu.addAction("&About…")
        about.triggered.connect(self._show_about)

    def _show_about(self) -> None:
        admin = "Administrator" if self.is_elevated else "Limited (not elevated)"
        from app.core.applog import log_file_path

        text = (
            f"<b>Windows Debloater &amp; Task Control</b><br>"
            f"Version: {__version__}<br><br>"
            f"Privileges: {admin}<br>"
            f"Log file: <code>{log_file_path()}</code><br><br>"
            f"A PySide6 utility for safely removing AppX packages, "
            f"controlling services and scheduled tasks, and reviewing "
            f"suspicious processes."
        )
        QMessageBox.about(self, "About Win Debloater", text)

    # ---- Tools: profiles, snapshots, diagnostics --------------------------------

    def _show_text_dialog(self, title: str, text: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(680, 440)
        lay = QVBoxLayout(dlg)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        lay.addWidget(buttons)
        dlg.exec()

    def _open_path(self, path) -> None:
        import os

        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            QMessageBox.information(self, "Path", str(path))

    def _export_profile(self) -> None:
        from app.core import profile

        path, _ = QFileDialog.getSaveFileName(
            self, "Export profile", "windebloater-profile.json", "JSON files (*.json)"
        )
        if not path:
            return
        self.statusBar().showMessage("Capturing profile…")
        self._profile_worker = FnWorker(profile.save_profile, path)
        self._profile_worker.succeeded.connect(
            lambda _p: self.statusBar().showMessage(f"Profile saved to {path}", 6000)
        )
        self._profile_worker.failed.connect(lambda m: QMessageBox.warning(self, "Export failed", m))
        self._profile_worker.start()

    def _apply_profile(self) -> None:
        from app.core import profile

        path, _ = QFileDialog.getOpenFileName(self, "Apply profile", "", "JSON files (*.json)")
        if not path:
            return
        try:
            prof = profile.load_profile(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Apply failed", str(exc))
            return
        n_svc = len(prof.get("services", {}))
        n_task = len(prof.get("tasks", {}))
        dry = " (dry-run)" if self.ctx.is_dry_run() else ""
        if (
            QMessageBox.question(
                self,
                "Apply profile",
                f"Apply this profile{dry}?\n\n"
                f"It may change up to {n_svc} service(s) and {n_task} task(s).\n"
                "Only items whose current value differs will be touched.",
            )
            != QMessageBox.Yes
        ):
            return
        self.statusBar().showMessage("Applying profile…")
        self._apply_worker = FnWorker(profile.apply_profile, prof)
        self._apply_worker.succeeded.connect(self._on_profile_applied)
        self._apply_worker.failed.connect(lambda m: QMessageBox.warning(self, "Apply failed", m))
        self._apply_worker.start()

    def _on_profile_applied(self, report) -> None:
        self.ctx.notify_log_changed()
        msg = (
            f"Applied: {report.services_changed} service(s), "
            f"{report.tasks_changed} task(s) changed; {report.skipped} unchanged."
        )
        if report.errors:
            msg += f"\n\n{len(report.errors)} error(s):\n" + "\n".join(report.errors[:10])
        self.statusBar().showMessage("Profile applied.", 6000)
        QMessageBox.information(self, "Profile applied", msg)

    def _save_snapshot(self) -> None:
        from app.core import profile

        self.statusBar().showMessage("Capturing snapshot…")
        self._snap_worker = FnWorker(profile.save_profile, str(profile.snapshot_path()))
        self._snap_worker.succeeded.connect(
            lambda _p: self.statusBar().showMessage("State snapshot saved.", 6000)
        )
        self._snap_worker.failed.connect(lambda m: QMessageBox.warning(self, "Snapshot failed", m))
        self._snap_worker.start()

    def _show_changes(self) -> None:
        from app.core import profile

        snap = profile.snapshot_path()
        if not snap.exists():
            QMessageBox.information(
                self,
                "No snapshot",
                "Save a state snapshot first (Tools → Save state snapshot).",
            )
            return
        self.statusBar().showMessage("Comparing to snapshot…")

        def work():
            old = profile.load_profile(str(snap))
            new = profile.capture_profile()
            return profile.diff_profiles(old, new)

        self._diff_worker = FnWorker(work)
        self._diff_worker.succeeded.connect(self._on_diff_ready)
        self._diff_worker.failed.connect(lambda m: QMessageBox.warning(self, "Compare failed", m))
        self._diff_worker.start()

    def _on_diff_ready(self, diff) -> None:
        from app.core import profile

        self.statusBar().clearMessage()
        if not profile.has_changes(diff):
            QMessageBox.information(self, "No changes", "Nothing changed since the last snapshot.")
            return
        self._show_text_dialog("Changes since snapshot", self._format_diff(diff))

    @staticmethod
    def _format_diff(diff: dict) -> str:
        lines: list[str] = []
        svc = diff.get("services", {})
        if svc:
            lines.append("Services (startup type):")
            for name, (old, new) in sorted(svc.items()):
                lines.append(f"  {name}: {old} → {new}")
            lines.append("")
        tasks = diff.get("tasks", {})
        if tasks:
            lines.append("Scheduled tasks (enabled):")
            for path, (old, new) in sorted(tasks.items()):
                lines.append(f"  {path}: {old} → {new}")
            lines.append("")
        if diff.get("appx_removed"):
            lines.append("AppX removed since snapshot:")
            lines += [f"  - {n}" for n in diff["appx_removed"]]
            lines.append("")
        if diff.get("appx_added"):
            lines.append("AppX added since snapshot:")
            lines += [f"  + {n}" for n in diff["appx_added"]]
            lines.append("")
        return "\n".join(lines).strip()

    def _edit_catalog_overlay(self) -> None:
        import json

        from app.core import appx

        path = appx.user_catalog_path()
        if not path.exists():
            template = {
                "version": appx.CATALOG_VERSION,
                "note": "User overlay. Entries here override or extend the bundled catalog by id.",
                "packages": [
                    {
                        "id": "Vendor.ExampleApp",
                        "name": "Example OEM App",
                        "category": "Third-Party",
                        "safe": True,
                        "description": "Example entry — edit or remove me.",
                    }
                ],
            }
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(template, indent=2), encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Catalog overlay", str(exc))
                return
        QMessageBox.information(
            self,
            "Catalog overlay",
            "Opening the user catalog overlay.\n\n"
            "Edit it, then use Refresh on the Bloatware tab to apply your changes.",
        )
        self._open_path(path)

    def _show_diagnostics(self) -> None:
        from app.core import diagnostics

        self.statusBar().showMessage("Collecting diagnostics…")
        self._diag_worker = FnWorker(diagnostics.as_text)
        self._diag_worker.succeeded.connect(
            lambda text: (
                self.statusBar().clearMessage(),
                self._show_text_dialog("Diagnostics", text),
            )
        )
        self._diag_worker.failed.connect(lambda m: QMessageBox.warning(self, "Diagnostics", m))
        self._diag_worker.start()

    def _collect_support_bundle(self) -> None:
        from app.core import support

        path, _ = QFileDialog.getSaveFileName(
            self, "Save support bundle", "windebloater-support.zip", "Zip files (*.zip)"
        )
        if not path:
            return
        actions = self.ctx.log.all()
        self.statusBar().showMessage("Building support bundle…")

        def work():
            return str(support.create_support_bundle(path, actions))

        self._bundle_worker = FnWorker(work)
        self._bundle_worker.succeeded.connect(
            lambda p: self.statusBar().showMessage(f"Support bundle saved to {p}", 8000)
        )
        self._bundle_worker.failed.connect(lambda m: QMessageBox.warning(self, "Support bundle", m))
        self._bundle_worker.start()

    def _check_for_updates(self, *, silent: bool = False) -> None:
        from app.core import updates

        if not silent:
            self.statusBar().showMessage("Checking for updates…")
        self._update_worker = FnWorker(updates.check_for_update)

        def on_ok(info) -> None:
            if not info:
                if not silent:
                    QMessageBox.information(
                        self, "Updates", "Could not check for updates right now."
                    )
                return
            if info["newer"]:
                self.statusBar().showMessage(f"Update available: v{info['latest']}", 0)
                if not silent:
                    self._show_update_dialog(info)
            elif not silent:
                self.statusBar().clearMessage()
                QMessageBox.information(self, "Updates", "You're on the latest version.")

        self._update_worker.succeeded.connect(on_ok)
        if not silent:
            self._update_worker.failed.connect(lambda m: QMessageBox.warning(self, "Updates", m))
        self._update_worker.start()

    def _show_update_dialog(self, info: dict) -> None:
        url = info.get("url") or ""
        QMessageBox.information(
            self,
            "Update available",
            f"A newer version (v{info['latest']}) is available.\n\n"
            f"Current version: v{__version__}\n\n"
            f"Download it from:\n{url}",
        )

    # ---- Keyboard shortcuts -----------------------------------------------------

    def _install_shortcuts(self) -> None:
        """Wire global and table-scoped keyboard shortcuts.

        Global (window) shortcuts are limited to non-destructive actions so
        they never interfere with text editing. Destructive shortcuts are
        scoped to each tab's table view via ``Qt.WidgetShortcut`` so they only
        fire when that table — not the search box — has focus.
        """
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("F5"), self, activated=self._refresh_current)

        self._bind_table_shortcut(
            self.bloatware_tab, "Ctrl+A", self.bloatware_tab._select_all_shown
        )
        self._bind_table_shortcut(self.bloatware_tab, "Delete", self.bloatware_tab._remove_selected)
        self._bind_table_shortcut(self.programs_tab, "Ctrl+A", self.programs_tab._select_all_shown)
        self._bind_table_shortcut(self.programs_tab, "Delete", self.programs_tab._remove_selected)
        self._bind_table_shortcut(
            self.services_tab, "Delete", lambda: self.services_tab._change_state(False)
        )
        self._bind_table_shortcut(self.tasks_tab, "Delete", lambda: self.tasks_tab._toggle(False))
        self._bind_table_shortcut(self.processes_tab, "Delete", self.processes_tab._kill)
        self._bind_table_shortcut(self.logs_tab, "Ctrl+Z", self.logs_tab._undo_selected)

    def _bind_table_shortcut(self, tab, sequence: str, slot) -> None:
        table = getattr(tab, "table", None)
        if table is None:
            return
        sc = QShortcut(QKeySequence(sequence), table)
        sc.setContext(Qt.WidgetShortcut)
        sc.activated.connect(slot)

    def _focus_search(self) -> None:
        tab = self.tabs.currentWidget()
        search = getattr(tab, "search", None)
        if search is not None:
            search.input.setFocus()
            search.input.selectAll()

    def _refresh_current(self) -> None:
        tab = self.tabs.currentWidget()
        if hasattr(tab, "reload"):
            tab.reload()

    # ---- Tab badges -------------------------------------------------------------

    def _install_badges(self) -> None:
        """Show live row counts in each tab label, e.g. "Bloatware (12)"."""
        self._tab_base = {i: self.tabs.tabText(i) for i in range(self.tabs.count())}
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            proxy = getattr(tab, "proxy", None)
            if proxy is None:
                continue
            proxy.rowsInserted.connect(lambda *a, i=index: self._update_badge(i))
            proxy.rowsRemoved.connect(lambda *a, i=index: self._update_badge(i))
            proxy.modelReset.connect(lambda i=index: self._update_badge(i))
            proxy.layoutChanged.connect(lambda *a, i=index: self._update_badge(i))

    def _update_badge(self, index: int) -> None:
        tab = self.tabs.widget(index)
        proxy = getattr(tab, "proxy", None)
        if proxy is None:
            return
        base = self._tab_base.get(index, self.tabs.tabText(index))
        n = proxy.rowCount()
        self.tabs.setTabText(index, f"{base} ({n})" if n else base)

    # ---- QSettings persistence --------------------------------------------------

    def _restore_settings(self) -> None:
        """Restore window/UI state from QSettings (best-effort)."""
        s = self._settings
        geom = s.value("geometry")
        if isinstance(geom, (bytes, bytearray)):
            self.restoreGeometry(geom)
        elif geom is not None:
            try:
                self.restoreGeometry(geom)
            except Exception:  # noqa: BLE001
                pass

        # Booleans round-trip as strings via QSettings on some backends.
        def _as_bool(value, default: bool) -> bool:
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in {"1", "true", "yes", "on"}
            return bool(value)

        self.advanced_toggle.setChecked(_as_bool(s.value("advanced_mode"), False))
        self.restore_toggle.setChecked(_as_bool(s.value("restore_point"), True))
        self.dryrun_toggle.setChecked(_as_bool(s.value("dry_run"), False))
        dryrun.set_enabled(self.dryrun_toggle.isChecked())

        tab_index = s.value("tab_index")
        try:
            idx = int(tab_index) if tab_index is not None else 0
        except (TypeError, ValueError):
            idx = 0
        if 0 <= idx < self.tabs.count():
            self.tabs.setCurrentIndex(idx)

        susp = getattr(self.processes_tab, "suspicious_only", None)
        if susp is not None:
            susp.setChecked(_as_bool(s.value("suspicious_only"), False))

        # Theme was applied in __init__ from settings; ensure the menu radio matches.
        act = self._theme_actions.get(self._theme)
        if act is not None and not act.isChecked():
            act.setChecked(True)

        # Restore per-tab table header layout (column widths/visibility/sort).
        for i in range(self.tabs.count()):
            table = getattr(self.tabs.widget(i), "table", None)
            if table is None:
                continue
            state = s.value(f"header_state_{i}")
            if state is not None:
                try:
                    table.horizontalHeader().restoreState(state)
                except Exception:  # noqa: BLE001
                    pass

    def _save_settings(self) -> None:
        s = self._settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("advanced_mode", self.advanced_toggle.isChecked())
        s.setValue("restore_point", self.restore_toggle.isChecked())
        s.setValue("dry_run", self.dryrun_toggle.isChecked())
        s.setValue("tab_index", self.tabs.currentIndex())
        s.setValue("theme", self._theme)
        for i in range(self.tabs.count()):
            table = getattr(self.tabs.widget(i), "table", None)
            if table is not None:
                s.setValue(f"header_state_{i}", table.horizontalHeader().saveState())
        susp = getattr(self.processes_tab, "suspicious_only", None)
        if susp is not None:
            s.setValue("suspicious_only", susp.isChecked())
        s.sync()

    def closeEvent(self, event) -> None:  # noqa: N802
        """Wait for any running background workers so we never abort on exit."""
        from PySide6.QtCore import QThread

        tabs = [
            self.bloatware_tab,
            self.programs_tab,
            self.services_tab,
            self.tasks_tab,
            self.processes_tab,
        ]
        for tab in tabs:
            for attr in ("_worker", "_batch", "_rp_worker"):
                worker = getattr(tab, attr, None)
                if isinstance(worker, QThread) and worker.isRunning():
                    if hasattr(worker, "cancel"):
                        worker.cancel()
                    worker.wait(3000)
        self._save_settings()
        event.accept()
