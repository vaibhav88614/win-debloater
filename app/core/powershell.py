"""Safe PowerShell execution helpers with JSON parsing."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any


# CREATE_NO_WINDOW prevents console windows from flashing when frozen.
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class PSResult:
    """Result of a PowerShell invocation."""

    ok: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""
    data: Any = None  # Parsed JSON when requested.
    error: str = ""

    @property
    def items(self) -> list[dict]:
        """Return parsed JSON normalized to a list of dicts."""
        if self.data is None:
            return []
        if isinstance(self.data, list):
            return [d for d in self.data if isinstance(d, dict)]
        if isinstance(self.data, dict):
            return [self.data]
        return []


def _powershell_exe() -> str:
    return "powershell.exe"


def run(
    script: str,
    *,
    timeout: int = 120,
    as_json: bool = False,
) -> PSResult:
    """Run a PowerShell script block and capture output.

    Args:
        script: PowerShell source to execute.
        timeout: Seconds before the call is aborted.
        as_json: When True, the script's stdout is parsed as JSON.
    """
    if sys.platform != "win32":
        return PSResult(ok=False, returncode=-1, error="PowerShell is only available on Windows.")

    cmd = [
        _powershell_exe(),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return PSResult(ok=False, returncode=-1, error=f"PowerShell timed out after {timeout}s.")
    except FileNotFoundError:
        return PSResult(ok=False, returncode=-1, error="powershell.exe was not found.")
    except Exception as exc:  # noqa: BLE001
        return PSResult(ok=False, returncode=-1, error=str(exc))

    result = PSResult(
        ok=proc.returncode == 0,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )

    if not result.ok and result.stderr:
        result.error = result.stderr.strip()

    if as_json and result.stdout.strip():
        try:
            result.data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            result.error = result.error or f"Failed to parse JSON: {exc}"
            result.ok = False

    return result


def run_json(script: str, *, timeout: int = 120) -> PSResult:
    """Convenience wrapper that wraps ``script`` output in ConvertTo-Json.

    The provided script should produce objects on the pipeline; this helper
    appends a depth-limited ConvertTo-Json so results are easy to parse.
    """
    wrapped = (
        "$ErrorActionPreference='Stop';"
        "$ProgressPreference='SilentlyContinue';"
        f"$out = @({script});"
        "$out | ConvertTo-Json -Depth 4 -Compress"
    )
    return run(wrapped, timeout=timeout, as_json=True)
