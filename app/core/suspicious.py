"""Heuristic detection of suspicious processes."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

from app.core import powershell as ps
from app.core.processes import ProcessInfo

# Directories that are unusual locations for legitimate background software.
_SUSPICIOUS_DIR_TOKENS = (
    "\\temp\\",
    "\\tmp\\",
    "\\appdata\\local\\temp",
    "\\downloads\\",
    "\\$recycle.bin\\",
    "\\windows\\temp\\",
    "\\public\\",
    "\\programdata\\temp",
)

# System binaries that should only ever live in System32 / SysWOW64.
_SYSTEM_BINARIES = {
    "svchost.exe",
    "csrss.exe",
    "lsass.exe",
    "services.exe",
    "winlogon.exe",
    "wininit.exe",
    "smss.exe",
    "explorer.exe",
    "spoolsv.exe",
    "taskhostw.exe",
    "dwm.exe",
    "conhost.exe",
    "rundll32.exe",
}

_SYSTEM_DIRS = ("\\windows\\system32", "\\windows\\syswow64", "\\windows\\")

# Trusted install roots; executables here are skipped during signature
# verification to keep the scan fast (these are rarely the malicious ones).
_TRUSTED_DIRS = (
    "\\windows\\",
    "\\program files\\",
    "\\program files (x86)\\",
    "\\programdata\\microsoft\\",
)


def get_autostart_targets() -> set[str]:
    """Collect lowercased autostart command targets (Run keys + Startup folders)."""
    script = r"""
$items = New-Object System.Collections.Generic.List[string]
$runKeys = @(
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run',
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\RunOnce',
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce',
  'HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run'
)
foreach ($k in $runKeys) {
  if (Test-Path $k) {
    (Get-Item $k).Property | ForEach-Object {
      $v = (Get-ItemProperty -Path $k -Name $_).$_
      if ($v) { $items.Add([string]$v) }
    }
  }
}
$startupDirs = @(
  [Environment]::GetFolderPath('Startup'),
  [Environment]::GetFolderPath('CommonStartup')
)
foreach ($d in $startupDirs) {
  if ($d -and (Test-Path $d)) {
    Get-ChildItem -Path $d -File -ErrorAction SilentlyContinue | ForEach-Object { $items.Add($_.FullName) }
  }
}
$items
"""
    res = ps.run_json(script, timeout=60)
    targets: set[str] = set()
    if res.ok and res.data:
        values = res.data if isinstance(res.data, list) else [res.data]
        for v in values:
            if isinstance(v, str):
                targets.add(v.lower())
    return targets


def get_signatures(paths: Iterable[str]) -> dict[str, str]:
    """Return {path_lower: signature_status} for the given executable paths.

    Status is the Authenticode status string (e.g. 'Valid', 'NotSigned').
    """
    unique = sorted({p for p in paths if p})
    if not unique:
        return {}

    # Build a PowerShell array literal of paths.
    escaped = ",".join(ps.ps_quote(p) for p in unique)
    script = (
        f"@({escaped}) | ForEach-Object {{ "
        "$s = try { (Get-AuthenticodeSignature -LiteralPath $_).Status } catch { 'Unknown' }; "
        "[pscustomobject]@{ Path=$_; Status=[string]$s } }"
    )
    res = ps.run_json(script, timeout=180)
    result: dict[str, str] = {}
    for item in res.items:
        path = (item.get("Path") or "").lower()
        status = item.get("Status") or "Unknown"
        if path:
            result[path] = status
    return result


def _looks_random(name: str) -> bool:
    """Detect random-looking executable names (high entropy / digit-heavy).

    Tightened to reduce false positives:
      * basename length >= 10 (so ``7zG.exe``, ``ms-teams.exe`` are spared),
      * flag if > 40% of basename is digits, OR
      * flag if Shannon entropy > 3.6 *and* vowel ratio < 25% of letters
        (legitimate names contain plenty of vowels; random strings don't).
    """
    base = re.sub(r"\.(exe|dll|scr|com)$", "", name.lower())
    if len(base) < 10:
        return False
    digits = sum(c.isdigit() for c in base)
    digit_heavy = digits / len(base) > 0.4
    letters = [c for c in base if c.isalpha()]
    if letters:
        vowel_ratio = sum(c in "aeiou" for c in letters) / len(letters)
    else:
        vowel_ratio = 0.0
    # Shannon entropy on the basename.
    counts: dict[str, int] = {}
    for ch in base:
        counts[ch] = counts.get(ch, 0) + 1
    entropy = -sum((c / len(base)) * math.log2(c / len(base)) for c in counts.values())
    high_entropy = entropy > 3.6
    return digit_heavy or (high_entropy and vowel_ratio < 0.25)


def _in_suspicious_dir(exe: str) -> bool:
    low = exe.lower()
    return any(tok in low for tok in _SUSPICIOUS_DIR_TOKENS)


def analyze(
    procs: list[ProcessInfo],
    *,
    verify_signatures: bool = True,
) -> list[ProcessInfo]:
    """Score processes for suspiciousness; mutates and returns the list."""
    autostart = get_autostart_targets()

    signatures: dict[str, str] = {}
    if verify_signatures:
        # Only verify executables outside trusted install roots; this keeps the
        # scan fast while still catching binaries in user-writable locations.
        candidate_paths = [
            p.exe for p in procs if p.exe and not any(d in p.exe.lower() for d in _TRUSTED_DIRS)
        ]
        signatures = get_signatures(candidate_paths)

    for p in procs:
        score = 0
        reasons: list[str] = []
        exe_low = p.exe.lower() if p.exe else ""
        name_low = p.name.lower()

        if p.is_protected:
            p.suspicion_score = 0
            p.reasons = []
            continue

        # 1. Unusual directory.
        if exe_low and _in_suspicious_dir(exe_low):
            score += 30
            reasons.append("Runs from a temp/downloads/temporary folder")

        # 2. System binary impersonation (wrong location).
        if name_low in _SYSTEM_BINARIES and exe_low:
            if not any(d in exe_low for d in _SYSTEM_DIRS):
                score += 45
                reasons.append("System binary name running from a non-system path")

        # 3. Signature status.
        if exe_low and exe_low in signatures:
            status = signatures[exe_low]
            if status not in ("Valid", "Unknown"):
                score += 35
                reasons.append(f"Executable signature is '{status}'")

        # 4. No executable path but consuming resources / networking.
        if not exe_low and not p.is_protected:
            if p.num_connections > 0:
                score += 20
                reasons.append("No readable executable path but has network connections")
            else:
                score += 8
                reasons.append("No readable executable path")

        # 5. Random-looking name.
        if _looks_random(name_low):
            score += 18
            reasons.append("Random-looking process name")

        # 6. Networking from a suspicious location.
        if p.num_connections > 0 and exe_low and _in_suspicious_dir(exe_low):
            score += 12
            reasons.append("Network activity from a suspicious location")

        # 7. Autostart from a suspicious location.
        if exe_low and exe_low in autostart and _in_suspicious_dir(exe_low):
            score += 15
            reasons.append("Auto-starts from a suspicious location")

        # 8. High sustained resource usage as a weak signal.
        if p.cpu_percent >= 60:
            score += 6
            reasons.append(f"High CPU usage ({p.cpu_percent}%)")

        p.suspicion_score = min(score, 100)
        p.reasons = reasons

    procs.sort(key=lambda x: x.suspicion_score, reverse=True)
    return procs
