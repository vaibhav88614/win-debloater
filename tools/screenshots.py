"""Generate documentation screenshots of each tab (best-effort, offscreen).

Populates every tab with synthetic sample data and grabs a PNG per tab into
``docs/img/``. Runs headless via the Qt ``offscreen`` platform, so it works in
CI; note that without system fonts the text rendering is approximate.

Usage:
    python tools/screenshots.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Ensure the project root is importable when run as a script.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import (  # noqa: E402
    appx,
    processes,
    services,  # noqa: E402
)
from app.core import scheduled_tasks as st  # noqa: E402
from app.core.actionlog import Action  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "docs" / "img"


def _sample_packages():
    return [
        appx.AppxPackage(
            name="Microsoft.BingNews",
            full_name="Microsoft.BingNews_1.0_x64",
            friendly_name="Microsoft News",
            category="News & Info",
            safe=True,
            description="MSN News app.",
        ),
        appx.AppxPackage(
            name="Microsoft.XboxGamingOverlay",
            full_name="Microsoft.XboxGamingOverlay_2.0_x64",
            friendly_name="Xbox Game Bar",
            category="Xbox",
            safe=False,
            description="Game Bar overlay.",
        ),
        appx.AppxPackage(
            name="Microsoft.MicrosoftEdge.Stable",
            full_name="",
            friendly_name="Microsoft Edge",
            category="Browser",
            safe=False,
            is_non_removable=True,
            description="Edge browser.",
        ),
    ]


def _sample_services():
    return [
        services.ServiceInfo(
            name="DiagTrack",
            display_name="Connected User Experiences",
            status="Running",
            start_type="Auto",
            description="Telemetry.",
            is_safe_tweak=True,
        ),
        services.ServiceInfo(
            name="Spooler",
            display_name="Print Spooler",
            status="Running",
            start_type="Auto",
            description="Printing.",
        ),
        services.ServiceInfo(
            name="wuauserv",
            display_name="Windows Update",
            status="Running",
            start_type="Manual",
            description="Updates.",
            is_protected=True,
        ),
    ]


def _sample_tasks():
    return [
        st.TaskInfo(
            name="Microsoft Compatibility Appraiser",
            path="\\Microsoft\\Windows\\Application Experience\\",
            full_path="\\Microsoft\\Windows\\Application Experience\\Microsoft Compatibility Appraiser",
            state="Ready",
            description="Telemetry collector.",
            author="Microsoft",
            is_telemetry=True,
        ),
        st.TaskInfo(
            name="MyTask",
            path="\\",
            full_path="\\MyTask",
            state="Disabled",
            description="A user task.",
            author="user",
        ),
    ]


def _sample_processes():
    return [
        processes.ProcessInfo(
            pid=1234,
            name="suspicious.exe",
            exe="C:/Temp/suspicious.exe",
            username="USER",
            cpu_percent=12.5,
            memory_mb=88.0,
            num_connections=3,
            suspicion_score=72,
            reasons=["unsigned binary", "temp path"],
        ),
        processes.ProcessInfo(
            pid=4321,
            name="explorer.exe",
            exe="C:/Windows/explorer.exe",
            username="USER",
            cpu_percent=1.2,
            memory_mb=140.0,
            is_protected=True,
            suspicion_score=0,
        ),
    ]


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow(is_elevated=True)
    win.resize(1180, 760)
    win._set_theme("dark")  # deterministic look for docs

    win.bloatware_tab.model.set_objects(_sample_packages())
    win.services_tab.model.set_objects(_sample_services())
    win.tasks_tab.model.set_objects(_sample_tasks())
    win.processes_tab.model.set_objects(_sample_processes())
    for kind, target, summary, ok in [
        ("appx_remove", "Microsoft News", "Removed AppX 'Microsoft.BingNews'", True),
        ("service_state", "Print Spooler", "Stopped service 'Spooler'", True),
        ("task_toggle", "Appraiser", "Disabled scheduled task", False),
    ]:
        win.ctx.log.add(Action(kind=kind, target=target, summary=summary, success=ok, undoable=ok))
    win.logs_tab.reload()

    OUT.mkdir(parents=True, exist_ok=True)
    tabs = ["bloatware", "services", "tasks", "processes", "history"]
    for i, name in enumerate(tabs):
        win.tabs.setCurrentIndex(i)
        app.processEvents()
        time.sleep(0.05)
        app.processEvents()
        path = OUT / f"{name}.png"
        win.grab().save(str(path))
        print(f"saved {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
