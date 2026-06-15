"""Export/import a tweak *profile* and diff system state.

A profile is a JSON snapshot of the parts of the system this tool manages:
service startup types, scheduled-task enabled states, and the set of installed
AppX packages (for reference/diffing). Profiles let a user replicate a setup on
another machine or review what changed since a saved snapshot.

Applying a profile only touches items whose current value differs from the
profile, and it goes through the normal core mutators — so dry-run mode and the
action log apply automatically when the caller wires them up.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app import __version__
from app.core import appx, scheduled_tasks, services
from app.core.applog import get_logger
from app.core.paths import user_data_dir

PROFILE_VERSION = 1
SNAPSHOT_NAME = "last_state.json"


def snapshot_path() -> Path:
    """Path of the auto-saved state snapshot used by the diff view."""
    return user_data_dir() / SNAPSHOT_NAME


def capture_profile() -> dict:
    """Capture the current managed system state as a profile dict."""
    svc = {s.name: s.start_type for s in services.list_services()}
    tasks = {t.full_path: t.enabled for t in scheduled_tasks.list_tasks()}
    appx_present = sorted({p.name for p in appx.list_installed(force=True)})
    return {
        "version": PROFILE_VERSION,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app_version": __version__,
        "services": svc,
        "tasks": tasks,
        "appx_present": appx_present,
    }


def save_profile(path: str | Path, profile: dict | None = None) -> dict:
    """Write ``profile`` (or a fresh capture) to ``path`` as JSON."""
    profile = profile or capture_profile()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, indent=2, ensure_ascii=False)
    return profile


def load_profile(path: str | Path) -> dict:
    """Load and lightly validate a profile JSON file."""
    log = get_logger()
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("Profile file is not a JSON object.")
    if data.get("version") != PROFILE_VERSION:
        log.warning(
            "Profile version mismatch: expected %s, got %r", PROFILE_VERSION, data.get("version")
        )
    data.setdefault("services", {})
    data.setdefault("tasks", {})
    data.setdefault("appx_present", [])
    return data


@dataclass
class ApplyReport:
    services_changed: int = 0
    tasks_changed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_changed(self) -> int:
        return self.services_changed + self.tasks_changed


def apply_profile(profile: dict, *, progress: Callable[[str], None] | None = None) -> ApplyReport:
    """Apply a profile, changing only items whose current value differs.

    Goes through ``services.set_start_type`` / ``scheduled_tasks.set_enabled``
    so dry-run mode is honoured. ``progress`` (optional) is called with a short
    status string for each applied change.
    """
    report = ApplyReport()

    current_services = {s.name: s for s in services.list_services()}
    for name, want_type in (profile.get("services") or {}).items():
        svc = current_services.get(name)
        if svc is None:
            report.skipped += 1
            continue
        if svc.is_protected or svc.start_type == want_type:
            report.skipped += 1
            continue
        res = services.set_start_type(name, want_type)
        if res.ok:
            report.services_changed += 1
            if progress:
                progress(f"Service '{name}' → {want_type}")
        else:
            report.errors.append(f"{name}: {res.error or 'failed'}")

    current_tasks = {t.full_path: t for t in scheduled_tasks.list_tasks()}
    for full_path, want_enabled in (profile.get("tasks") or {}).items():
        task = current_tasks.get(full_path)
        if task is None:
            report.skipped += 1
            continue
        if task.enabled == want_enabled:
            report.skipped += 1
            continue
        res = scheduled_tasks.set_enabled(task.path, task.name, bool(want_enabled))
        if res.ok:
            report.tasks_changed += 1
            if progress:
                state = "enabled" if want_enabled else "disabled"
                progress(f"Task '{full_path}' → {state}")
        else:
            report.errors.append(f"{full_path}: {res.error or 'failed'}")

    return report


def diff_profiles(old: dict, new: dict) -> dict:
    """Return the differences between two profiles.

    Result keys:
      - ``services``: {name: (old_type, new_type)}
      - ``tasks``: {full_path: (old_enabled, new_enabled)}
      - ``appx_removed``: [names present in old, absent in new]
      - ``appx_added``: [names present in new, absent in old]
    """
    old_svc = old.get("services", {}) or {}
    new_svc = new.get("services", {}) or {}
    svc_changes = {
        name: (old_svc.get(name), new_svc.get(name))
        for name in set(old_svc) | set(new_svc)
        if old_svc.get(name) != new_svc.get(name)
    }

    old_tasks = old.get("tasks", {}) or {}
    new_tasks = new.get("tasks", {}) or {}
    task_changes = {
        path: (old_tasks.get(path), new_tasks.get(path))
        for path in set(old_tasks) | set(new_tasks)
        if old_tasks.get(path) != new_tasks.get(path)
    }

    old_appx = set(old.get("appx_present", []) or [])
    new_appx = set(new.get("appx_present", []) or [])
    return {
        "services": svc_changes,
        "tasks": task_changes,
        "appx_removed": sorted(old_appx - new_appx),
        "appx_added": sorted(new_appx - old_appx),
    }


def has_changes(diff: dict) -> bool:
    return bool(
        diff.get("services")
        or diff.get("tasks")
        or diff.get("appx_removed")
        or diff.get("appx_added")
    )
