"""Process-wide dry-run toggle.

When dry-run is enabled, every mutating core function returns a synthetic
``PSResult`` instead of actually invoking PowerShell. This lets users preview
what a click would do without changing the system. The UI also annotates the
action log so dry-run entries are visibly distinct and non-undoable.
"""

from __future__ import annotations

_enabled: bool = False

DRY_RUN_MARKER = "dry-run"


def is_enabled() -> bool:
    return _enabled


def set_enabled(value: bool) -> None:
    global _enabled
    _enabled = bool(value)


def dry_result(message: str):
    """Build a synthetic success ``PSResult`` for dry-run paths.

    Imported lazily to avoid a circular import (powershell -> applog -> ...).
    """
    from app.core.powershell import PSResult

    return PSResult(ok=True, returncode=0, stdout=f"{DRY_RUN_MARKER}: {message}")
