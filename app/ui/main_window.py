"""Main application window with tabs and global controls."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import __version__
from app.core.actionlog import ActionLog
from app.core.elevation import is_admin
from app.core.paths import resource_path
from app.ui.bloatware_tab import BloatwareTab
from app.ui.logs_tab import LogsTab
from app.ui.processes_tab import ProcessesTab
from app.ui.services_tab import ServicesTab
from app.ui.tasks_tab import TasksTab


class AppContext:
    """Shared state passed to every tab."""

    def __init__(self, window: "MainWindow") -> None:
        self._window = window
        self.log = ActionLog()

    def is_advanced(self) -> bool:
        return self._window.advanced_toggle.isChecked()

    def want_restore_point(self) -> bool:
        return self._window.restore_toggle.isChecked()

    def notify_log_changed(self) -> None:
        self._window.logs_tab.reload()

    def set_status(self, text: str) -> None:
        self._window.statusBar().showMessage(text, 6000)


class MainWindow(QWidget):
    def __init__(self, is_elevated: bool | None = None) -> None:
        super().__init__()
        self.is_elevated = is_admin() if is_elevated is None else is_elevated
        self.setWindowTitle(f"Windows Debloater & Task Control  v{__version__}")
        self.resize(1100, 720)
        self.setMinimumSize(900, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        root.addLayout(self._build_topbar())

        if not self.is_elevated:
            root.addWidget(self._build_admin_banner())

        self.ctx = AppContext(self)

        self.tabs = QTabWidget()
        self.bloatware_tab = BloatwareTab(self.ctx)
        self.services_tab = ServicesTab(self.ctx)
        self.tasks_tab = TasksTab(self.ctx)
        self.processes_tab = ProcessesTab(self.ctx)
        self.logs_tab = LogsTab(self.ctx)
        self.tabs.addTab(self.bloatware_tab, "Bloatware")
        self.tabs.addTab(self.services_tab, "Services")
        self.tabs.addTab(self.tasks_tab, "Scheduled Tasks")
        self.tabs.addTab(self.processes_tab, "Processes & Suspicious")
        self.tabs.addTab(self.logs_tab, "History")
        root.addWidget(self.tabs, 1)

        self._apply_style()

    def _build_topbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        title = QLabel("Windows Debloater & Task Control")
        title.setObjectName("title")
        bar.addWidget(title)
        bar.addStretch(1)

        self.restore_toggle = QCheckBox("Restore point before changes")
        self.restore_toggle.setChecked(True)
        bar.addWidget(self.restore_toggle)

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
        for tab in (self.bloatware_tab, self.services_tab, self.tasks_tab):
            if hasattr(tab, "_populate"):
                tab._populate()
        if self.advanced_toggle.isChecked():
            self.statusBar_message("Advanced mode enabled - extra caution advised.")

    def statusBar_message(self, text: str) -> None:
        self.setWindowTitle(
            f"Windows Debloater & Task Control  v{__version__}  -  {text}"
        )

    def _apply_style(self) -> None:
        try:
            qss = resource_path("app", "resources", "style.qss").read_text(encoding="utf-8")
            self.setStyleSheet(qss)
        except OSError:
            pass

    # statusBar() shim so AppContext.set_status works on a QWidget root.
    def statusBar(self):  # noqa: N802
        class _Shim:
            def showMessage(_self, text, _timeout=0):
                self.statusBar_message(text)
        return _Shim()

    def closeEvent(self, event) -> None:  # noqa: N802
        """Wait for any running background workers so we never abort on exit."""
        from PySide6.QtCore import QThread

        tabs = [
            self.bloatware_tab, self.services_tab, self.tasks_tab,
            self.processes_tab,
        ]
        for tab in tabs:
            for attr in ("_worker", "_batch"):
                worker = getattr(tab, attr, None)
                if isinstance(worker, QThread) and worker.isRunning():
                    if hasattr(worker, "cancel"):
                        worker.cancel()
                    worker.wait(3000)
        event.accept()
