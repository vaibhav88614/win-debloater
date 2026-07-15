"""Safe PowerShell execution helpers with JSON parsing."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from app.core.applog import get_logger

# CREATE_NO_WINDOW prevents console windows from flashing when frozen.
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Forces PowerShell to emit UTF-8 on stdout so non-ASCII service/AppX names
# survive the round-trip through subprocess.
_UTF8_PRELUDE = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"

# Cap script previews in the log so the file stays readable.
_LOG_PREVIEW_CHARS = 240

# How often to emit an INFO "still running" heartbeat while a PS call is
# blocking. Small enough to notice a stuck call quickly (setup.exe uninstall
# can genuinely take a while), large enough not to spam the log.
_HEARTBEAT_INTERVAL_SEC = 20.0

# Registry of in-flight PowerShell child processes so a long-running call can
# be cancelled from another thread (e.g. a GUI "Cancel" button).
_active_lock = threading.Lock()
_active_procs: set[subprocess.Popen] = set()
_cancelled_pids: set[int] = set()


def _register(proc: subprocess.Popen) -> None:
    with _active_lock:
        _active_procs.add(proc)


def _unregister(proc: subprocess.Popen) -> None:
    with _active_lock:
        _active_procs.discard(proc)


def cancel_active() -> int:
    """Kill every in-flight PowerShell child process.

    Returns the number of processes signalled. Each killed process is recorded
    so its :func:`run` call reports a cancellation rather than a generic error.
    """
    with _active_lock:
        procs = list(_active_procs)
    killed = 0
    for proc in procs:
        try:
            _cancelled_pids.add(proc.pid)
            proc.kill()
            killed += 1
        except Exception:  # noqa: BLE001
            _cancelled_pids.discard(proc.pid)
    return killed


def ps_quote(value: str) -> str:
    """Quote a string for safe interpolation inside a single-quoted PS literal.

    Returns the value already wrapped in single quotes with any embedded
    apostrophes doubled. Use this for *every* user/system-supplied string
    that ends up in a PowerShell script.

        >>> ps_quote("Bing's News")
        "'Bing''s News'"
    """
    if value is None:
        return "''"
    return "'" + str(value).replace("'", "''") + "'"


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
    label: str | None = None,
) -> PSResult:
    """Run a PowerShell script block and capture output.

    Args:
        script: PowerShell source to execute.
        timeout: Seconds before the call is aborted.
        as_json: When True, the script's stdout is parsed as JSON.
        label: Optional short human label (e.g. "remove-by-name Foo.App").
            When set, start/finish log lines are emitted at INFO level so
            user-triggered operations are visible in the default log; without
            it (e.g. for repeated list queries) they stay at DEBUG.
    """
    if sys.platform != "win32":
        return PSResult(ok=False, returncode=-1, error="PowerShell is only available on Windows.")

    # Prepend the UTF-8 prelude so localized strings (e.g. service display
    # names) come back intact for JSON parsing.
    full_script = _UTF8_PRELUDE + script

    cmd = [
        _powershell_exe(),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        full_script,
    ]

    log = get_logger()
    preview = (script[:_LOG_PREVIEW_CHARS] + "…") if len(script) > _LOG_PREVIEW_CHARS else script
    start_level = log.info if label else log.debug
    finish_level_ok = log.info if label else log.debug
    tag = f"[{label}] " if label else ""

    start_level("PS starting %s(timeout=%ss): %s", tag, timeout, preview)
    started = time.monotonic()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        log.error("powershell.exe not found")
        return PSResult(ok=False, returncode=-1, error="powershell.exe was not found.")
    except Exception as exc:  # noqa: BLE001
        log.exception("PS execution error")
        return PSResult(ok=False, returncode=-1, error=str(exc))

    # Emit a periodic "still running" line at INFO so a stuck call is visible
    # without waiting for the timeout. Reschedules itself until stop_event fires.
    stop_event = threading.Event()

    def _heartbeat() -> None:
        if stop_event.is_set():
            return
        elapsed = int(time.monotonic() - started)
        log.info(
            "PS still running %s(elapsed=%ss, pid=%s, timeout=%ss): %s",
            tag,
            elapsed,
            proc.pid,
            timeout,
            preview,
        )
        if not stop_event.is_set():
            next_t = threading.Timer(_HEARTBEAT_INTERVAL_SEC, _heartbeat)
            next_t.daemon = True
            next_t.start()

    hb_timer = threading.Timer(_HEARTBEAT_INTERVAL_SEC, _heartbeat)
    hb_timer.daemon = True
    hb_timer.start()

    _register(proc)
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                # Bounded drain so a process that ignores kill() can't wedge
                # the worker thread forever.
                out, err = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                out, err = "", ""
            _cancelled_pids.discard(proc.pid)
            log.warning("PS timeout %safter %ss: %s", tag, timeout, preview)
            return PSResult(
                ok=False, returncode=-1, error=f"PowerShell timed out after {timeout}s."
            )
    finally:
        stop_event.set()
        hb_timer.cancel()
        _unregister(proc)

    # A process killed via cancel_active() surfaces as a cancellation.
    if proc.pid in _cancelled_pids:
        _cancelled_pids.discard(proc.pid)
        log.info("PS cancelled %s: %s", tag, preview)
        return PSResult(ok=False, returncode=-1, error="Cancelled.")

    elapsed_ms = int((time.monotonic() - started) * 1000)

    result = PSResult(
        ok=proc.returncode == 0,
        returncode=proc.returncode,
        stdout=out or "",
        stderr=err or "",
    )

    if not result.ok and result.stderr:
        result.error = result.stderr.strip()

    if as_json and result.stdout.strip():
        try:
            result.data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            result.error = result.error or f"Failed to parse JSON: {exc}"
            result.ok = False

    level = finish_level_ok if result.ok else log.warning
    level(
        "PS rc=%s %sin %sms: %s%s",
        result.returncode,
        tag,
        elapsed_ms,
        preview,
        f"  ERR: {result.error}" if result.error else "",
    )

    return result


def run_json(script: str, *, timeout: int = 120) -> PSResult:
    """Convenience wrapper that wraps ``script`` output in ConvertTo-Json.

    The provided script should produce objects on the pipeline; this helper
    runs it inside a script block and appends a depth-limited ConvertTo-Json
    so results are easy to parse. The ``PSResult.items`` property normalizes
    single objects vs. lists vs. empty pipelines.
    """
    wrapped = (
        "$ErrorActionPreference='Stop';"
        "$ProgressPreference='SilentlyContinue';"
        f"& {{ {script} }} | ConvertTo-Json -Depth 4 -Compress"
    )
    return run(wrapped, timeout=timeout, as_json=True)
