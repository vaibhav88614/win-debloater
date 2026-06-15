"""Administrator privilege detection and UAC self-elevation."""

from __future__ import annotations

import ctypes
import os
import sys


def is_admin() -> bool:
    """Return True if the current process has administrator rights."""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Relaunch the current program with a UAC elevation prompt.

    Returns True if an elevated process was launched (caller should exit),
    False if elevation failed or was declined.
    """
    if os.name != "nt":
        return False

    # Build the command line that re-runs this program.
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller-built .exe
        executable = sys.executable
        params = subprocess_args(sys.argv[1:])
    else:
        executable = sys.executable
        params = subprocess_args([os.path.abspath(sys.argv[0]), *sys.argv[1:]])

    try:
        # ShellExecuteW with the "runas" verb triggers the UAC prompt.
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
        # Values > 32 indicate success.
        return int(ret) > 32
    except Exception:
        return False


def subprocess_args(args: list[str]) -> str:
    """Quote a list of arguments into a single command-line string."""
    quoted = []
    for arg in args:
        if not arg:
            quoted.append('""')
        elif " " in arg or '"' in arg:
            quoted.append('"' + arg.replace('"', r"\"") + '"')
        else:
            quoted.append(arg)
    return " ".join(quoted)


def ensure_admin(auto_elevate: bool = True) -> bool:
    """Ensure the process is elevated.

    If not elevated and ``auto_elevate`` is True, attempt to relaunch with UAC.
    Returns True if already elevated (continue running), False if a relaunch was
    triggered (the caller should exit immediately).
    """
    if is_admin():
        return True
    if auto_elevate and relaunch_as_admin():
        return False
    return True  # Could not elevate; run anyway in limited mode.
