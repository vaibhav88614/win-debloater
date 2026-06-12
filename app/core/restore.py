"""Create Windows System Restore points before destructive operations."""
from __future__ import annotations

from app.core import powershell as ps


def is_restore_enabled() -> bool:
    """Best-effort check whether System Restore is available on the system drive."""
    script = (
        "try { "
        "$d=(Get-CimInstance -Namespace root/default -ClassName SystemRestore "
        "-ErrorAction Stop); if ($d) { 'enabled' } else { 'unknown' } "
        "} catch { 'disabled' }"
    )
    res = ps.run(script, timeout=30)
    return "enabled" in (res.stdout or "").lower()


def create_restore_point(description: str = "Win Debloater change") -> ps.PSResult:
    """Create a system restore point.

    Requires administrator rights and System Protection enabled on the OS drive.
    """
    safe_desc = description.replace("'", " ")
    script = (
        "$ErrorActionPreference='Stop';"
        # Bypass the once-per-24h frequency limit so successive points work.
        "try { New-ItemProperty -Path "
        "'HKLM:\\Software\\Microsoft\\Windows NT\\CurrentVersion\\SystemRestore' "
        "-Name 'SystemRestorePointCreationFrequency' -Value 0 -PropertyType DWord "
        "-Force | Out-Null } catch {}; "
        "Enable-ComputerRestore -Drive $env:SystemDrive -ErrorAction SilentlyContinue; "
        f"Checkpoint-Computer -Description '{safe_desc}' "
        "-RestorePointType 'MODIFY_SETTINGS'"
    )
    res = ps.run(script, timeout=180)
    if not res.ok and not res.error:
        res.error = (
            "Could not create a restore point. Ensure System Protection is enabled "
            "for the system drive and that the app is running as Administrator."
        )
    return res
