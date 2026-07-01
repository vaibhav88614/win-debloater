"""Persistent action history enabling undo of reversible operations."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.paths import user_data_dir

# Action kinds.
KIND_APPX_REMOVE = "appx_remove"
KIND_PROGRAM_UNINSTALL = "program_uninstall"
KIND_SERVICE_STARTTYPE = "service_starttype"
KIND_SERVICE_STATE = "service_state"
KIND_TASK_TOGGLE = "task_toggle"
KIND_PROCESS_KILL = "process_kill"
KIND_PROCESS_SUSPEND = "process_suspend"
KIND_RESTORE_POINT = "restore_point"


@dataclass
class Action:
    kind: str
    target: str  # human-readable target name
    summary: str  # what happened
    timestamp: float = field(default_factory=time.time)
    success: bool = True
    undoable: bool = False
    undo_data: dict[str, Any] = field(default_factory=dict)
    undone: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Action:
        return Action(
            kind=d.get("kind", ""),
            target=d.get("target", ""),
            summary=d.get("summary", ""),
            timestamp=d.get("timestamp", time.time()),
            success=d.get("success", True),
            undoable=d.get("undoable", False),
            undo_data=d.get("undo_data", {}) or {},
            undone=d.get("undone", False),
        )

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))


class ActionLog:
    """JSON-backed log of actions, kept in the per-user data directory."""

    def __init__(self) -> None:
        self.path = user_data_dir() / "action_history.json"
        self._actions: list[Action] = []
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self._actions = [Action.from_dict(d) for d in raw]
        except (OSError, json.JSONDecodeError):
            self._actions = []

    def save(self) -> None:
        """Atomically persist the action list.

        Writes to ``<path>.tmp`` first, then replaces the real file. A crash
        mid-write therefore preserves the previous valid JSON.
        """
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump([a.to_dict() for a in self._actions], fh, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            # Best-effort cleanup of a stale temp file.
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    def add(self, action: Action) -> Action:
        # Dry-run actions are preview-only — never undoable, prefix the summary.
        try:
            from app.core import dryrun  # local import to avoid cycles

            if dryrun.is_enabled():
                action.undoable = False
                if not action.summary.startswith("(dry-run)"):
                    action.summary = f"(dry-run) {action.summary}"
        except Exception:  # noqa: BLE001
            pass
        self._actions.append(action)
        self.save()
        return action

    def all(self) -> list[Action]:
        return list(reversed(self._actions))  # newest first

    def undoable(self) -> list[Action]:
        return [a for a in self.all() if a.undoable and a.success and not a.undone]

    def mark_undone(self, action: Action) -> None:
        action.undone = True
        self.save()

    def clear(self) -> None:
        self._actions = []
        self.save()


def perform_undo(action: Action) -> tuple[bool, str]:
    """Reverse a previously recorded, undoable action.

    Returns (success, message). Imports core modules lazily to avoid cycles.
    """
    if not action.undoable or action.undone:
        return False, "This action cannot be undone."

    if action.kind == KIND_APPX_REMOVE:
        from app.core import appx

        pkg = appx.AppxPackage(
            name=action.undo_data.get("name", action.target),
            full_name=action.undo_data.get("full_name", ""),
        )
        res = appx.restore_package(pkg)
        return res.ok, (res.stdout.strip() or res.error or "Restore attempted.")

    if action.kind == KIND_SERVICE_STARTTYPE:
        from app.core import services

        name = action.undo_data.get("name", action.target)
        previous = action.undo_data.get("previous_start_type", "Manual")
        previous = _normalize_start_type(previous)
        res = services.set_start_type(name, previous)
        return res.ok, (res.error or f"Restored '{name}' start type to {previous}.")

    if action.kind == KIND_SERVICE_STATE:
        from app.core import services

        name = action.undo_data.get("name", action.target)
        was_running = action.undo_data.get("was_running", False)
        if was_running:
            res = services.start_service(name)
            return res.ok, (res.error or f"Restarted '{name}'.")
        res = services.stop_service(name)
        return res.ok, (res.error or f"Stopped '{name}'.")

    if action.kind == KIND_TASK_TOGGLE:
        from app.core import scheduled_tasks

        path = action.undo_data.get("path", "")
        name = action.undo_data.get("name", action.target)
        was_enabled = action.undo_data.get("was_enabled", True)
        res = scheduled_tasks.set_enabled(path, name, was_enabled)
        state = "enabled" if was_enabled else "disabled"
        return res.ok, (res.error or f"Task '{name}' re-{state}.")

    if action.kind == KIND_PROCESS_SUSPEND:
        from app.core import processes

        pid = action.undo_data.get("pid", 0)
        return processes.resume_process(int(pid))

    return False, "No undo handler for this action type."


def _normalize_start_type(value: str) -> str:
    mapping = {
        "Auto": "Automatic",
        "Automatic": "Automatic",
        "Manual": "Manual",
        "Disabled": "Disabled",
        "Boot": "Automatic",
        "System": "Automatic",
    }
    return mapping.get(
        value,
        value
        if value in ("Automatic", "Manual", "Disabled", "AutomaticDelayedStart")
        else "Manual",
    )
