"""Create Windows System Restore points before destructive operations."""

from __future__ import annotations

import sys

from app.core import dryrun
from app.core import powershell as ps


def is_restore_enabled() -> bool:
    """Best-effort check whether System Restore is enabled on the system drive.

    Probes in two ways and accepts either:
      1. ``Get-ComputerRestorePoint`` returns without error (the cmdlet is
         present and System Protection responds).
      2. The ``RPSessionInterval`` registry DWORD is non-zero.
    """
    if sys.platform != "win32":
        return False
    script = (
        "$result = 'disabled';"
        "try { Get-ComputerRestorePoint -ErrorAction Stop | Out-Null; $result='enabled' } catch {};"
        "if ($result -ne 'enabled') {"
        "  try {"
        "    $val = (Get-ItemProperty -Path "
        "'HKLM:\\Software\\Microsoft\\Windows NT\\CurrentVersion\\SystemRestore' "
        "-Name 'RPSessionInterval' -ErrorAction Stop).RPSessionInterval;"
        "    if ($val -gt 0) { $result='enabled' }"
        "  } catch {}"
        "};"
        "$result"
    )
    res = ps.run(script, timeout=30)
    return "enabled" in (res.stdout or "").lower()


def create_restore_point(description: str = "Win Debloater change") -> ps.PSResult:
    """Create a system restore point.

    Requires administrator rights and System Protection enabled on the OS drive.
    """
    if sys.platform != "win32":
        return ps.PSResult(
            ok=False, returncode=-1, error="System Restore is only available on Windows."
        )
    if dryrun.is_enabled():
        return dryrun.dry_result(f"would create restore point '{description}'")
    script = (
        "$ErrorActionPreference='Stop';"
        # Bypass the once-per-24h frequency limit so successive points work.
        "try { New-ItemProperty -Path "
        "'HKLM:\\Software\\Microsoft\\Windows NT\\CurrentVersion\\SystemRestore' "
        "-Name 'SystemRestorePointCreationFrequency' -Value 0 -PropertyType DWord "
        "-Force | Out-Null } catch {}; "
        "Enable-ComputerRestore -Drive $env:SystemDrive -ErrorAction SilentlyContinue; "
        f"Checkpoint-Computer -Description {ps.ps_quote(description)} "
        "-RestorePointType 'MODIFY_SETTINGS'"
    )
    res = ps.run(script, timeout=180)
    if not res.ok and not res.error:
        res.error = (
            "Could not create a restore point. Ensure System Protection is enabled "
            "for the system drive and that the app is running as Administrator."
        )
    return res
