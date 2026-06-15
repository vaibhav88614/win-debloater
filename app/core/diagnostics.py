"""Gather read-only system diagnostics for the About/Diagnostics dialog.

Everything here is best-effort and cross-platform-safe: Windows-only probes
(Defender, System Restore) are guarded and simply omitted elsewhere. Useful
when a user needs to attach environment details to a bug report.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys

from app import __version__
from app.core import applog, elevation, restore
from app.core import powershell as ps
from app.core.paths import user_data_dir


def _disk_usage() -> tuple[float, float]:
    """Return (free_gb, total_gb) for the system drive (best-effort)."""
    if sys.platform == "win32":
        root = os.environ.get("SystemDrive", "C:") + "\\"
    else:
        root = "/"
    try:
        usage = shutil.disk_usage(root)
        gb = 1024**3
        return round(usage.free / gb, 1), round(usage.total / gb, 1)
    except OSError:
        return 0.0, 0.0


def _defender_state() -> dict:
    """Query Windows Defender status via PowerShell (Windows only)."""
    if sys.platform != "win32":
        return {}
    res = ps.run_json(
        "Get-MpComputerStatus | Select-Object AMServiceEnabled, "
        "AntivirusEnabled, RealTimeProtectionEnabled, "
        "AntivirusSignatureLastUpdated",
        timeout=30,
    )
    if res.ok and res.items:
        d = res.items[0]
        return {
            "antivirus_enabled": d.get("AntivirusEnabled"),
            "realtime_enabled": d.get("RealTimeProtectionEnabled"),
            "service_enabled": d.get("AMServiceEnabled"),
            "signatures_updated": d.get("AntivirusSignatureLastUpdated"),
        }
    return {}


def collect() -> dict:
    """Collect a flat dict of diagnostic facts."""
    free_gb, total_gb = _disk_usage()
    info: dict = {
        "app_version": __version__,
        "python": platform.python_version(),
        "platform": sys.platform,
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.machine(),
        "administrator": elevation.is_admin(),
        "disk_free_gb": free_gb,
        "disk_total_gb": total_gb,
        "log_file": str(applog.log_file_path()),
        "user_data_dir": str(user_data_dir()),
    }
    if sys.platform == "win32":
        info["restore_enabled"] = restore.is_restore_enabled()
        info["defender"] = _defender_state()
    return info


def as_text(info: dict | None = None) -> str:
    """Render diagnostics as a readable plain-text block."""
    info = info or collect()
    lines = [
        f"App version       : {info.get('app_version')}",
        f"Python            : {info.get('python')}",
        f"OS                : {info.get('os')}",
        f"Machine           : {info.get('machine')}",
        f"Administrator     : {info.get('administrator')}",
        f"Disk free / total : {info.get('disk_free_gb')} / {info.get('disk_total_gb')} GB",
    ]
    if "restore_enabled" in info:
        lines.append(f"System Restore    : {'enabled' if info['restore_enabled'] else 'disabled'}")
    defender = info.get("defender") or {}
    if defender:
        lines.append(
            "Defender          : "
            f"AV={defender.get('antivirus_enabled')} "
            f"RTP={defender.get('realtime_enabled')} "
            f"Svc={defender.get('service_enabled')}"
        )
        if defender.get("signatures_updated"):
            lines.append(f"  signatures      : {defender.get('signatures_updated')}")
    lines.append(f"Log file          : {info.get('log_file')}")
    lines.append(f"User data dir     : {info.get('user_data_dir')}")
    return "\n".join(lines)
