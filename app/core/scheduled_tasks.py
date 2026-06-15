"""List and control Windows scheduled tasks."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from app.core import dryrun
from app.core import powershell as ps

# Telemetry / advertising scheduled tasks commonly disabled (Safe mode).
KNOWN_TELEMETRY_TASKS = {
    r"\microsoft\windows\application experience\microsoft compatibility appraiser",
    r"\microsoft\windows\application experience\programdataupdater",
    r"\microsoft\windows\application experience\startupapptask",
    r"\microsoft\windows\customer experience improvement program\consolidator",
    r"\microsoft\windows\customer experience improvement program\usbceip",
    r"\microsoft\windows\customer experience improvement program\uploadertask",
    r"\microsoft\windows\autochk\proxy",
    r"\microsoft\windows\feedback\siuf\dmclient",
    r"\microsoft\windows\feedback\siuf\dmclientonscenariodownload",
    r"\microsoft\windows\windows error reporting\queuereporting",
    r"\microsoft\windows\diskdiagnostic\microsoft-windows-diskdiagnosticdatacollector",
}


@dataclass
class TaskInfo:
    name: str  # Task name
    path: str  # Folder path
    full_path: str  # path + name
    state: str = ""  # Ready / Disabled / Running
    description: str = ""
    author: str = ""
    is_telemetry: bool = False

    @property
    def safe(self) -> bool:
        return self.is_telemetry

    @property
    def enabled(self) -> bool:
        return self.state.lower() != "disabled"


def list_tasks() -> list[TaskInfo]:
    """List scheduled tasks with their state."""
    if sys.platform != "win32":
        return []
    script = (
        "Get-ScheduledTask | Select-Object TaskName, TaskPath, State, "
        "@{N='Description';E={$_.Description}}, @{N='Author';E={$_.Author}}"
    )
    res = ps.run_json(script, timeout=120)
    tasks: list[TaskInfo] = []
    if not res.ok:
        return tasks

    for item in res.items:
        name = (item.get("TaskName") or "").strip()
        path = (item.get("TaskPath") or "").strip()
        if not name:
            continue
        full = (path + name).lower()
        state = str(item.get("State") or "")
        # CIM State enum sometimes comes back as an int; normalize.
        state = _normalize_state(state)
        tasks.append(
            TaskInfo(
                name=name,
                path=path,
                full_path=path + name,
                state=state,
                description=(item.get("Description") or "").strip(),
                author=(item.get("Author") or "").strip(),
                is_telemetry=full in KNOWN_TELEMETRY_TASKS,
            )
        )

    tasks.sort(key=lambda t: t.full_path.lower())
    return tasks


def _normalize_state(state: str) -> str:
    mapping = {"1": "Disabled", "2": "Queued", "3": "Ready", "4": "Running"}
    return mapping.get(state, state)


def set_enabled(path: str, name: str, enabled: bool) -> ps.PSResult:
    """Enable or disable a scheduled task."""
    if dryrun.is_enabled():
        verb = "enable" if enabled else "disable"
        return dryrun.dry_result(f"would {verb} task '{path}{name}'")
    verb = "Enable-ScheduledTask" if enabled else "Disable-ScheduledTask"
    script = (
        f"{verb} -TaskPath {ps.ps_quote(path)} -TaskName {ps.ps_quote(name)} "
        "-ErrorAction Stop | Out-Null"
    )
    return ps.run(script, timeout=60)
