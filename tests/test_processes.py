"""Process helpers - protections and metadata."""

from __future__ import annotations

import os

from app.core import processes


def test_protected_set_includes_critical_processes():
    for name in ("system", "csrss.exe", "lsass.exe", "winlogon.exe", "smss.exe"):
        assert name in processes.PROTECTED_PROCESSES


def test_kill_protected_process_is_rejected():
    # PID 4 is "System" on Windows. On non-Windows / unusual systems we
    # fall back to just checking the helper's behaviour with our own PID
    # by marking it via the PROTECTED_PROCESSES set.
    import psutil

    me = psutil.Process(os.getpid())
    original_name = me.name().lower()
    processes.PROTECTED_PROCESSES.add(original_name)
    try:
        ok, msg = processes.kill_process(os.getpid())
        assert not ok
        assert "protected" in msg.lower()
    finally:
        # Only remove if we added it (don't accidentally drop a real entry).
        if original_name not in {
            "system",
            "system idle process",
            "registry",
            "smss.exe",
            "csrss.exe",
            "wininit.exe",
            "services.exe",
            "lsass.exe",
            "winlogon.exe",
            "fontdrvhost.exe",
            "dwm.exe",
            "svchost.exe",
            "explorer.exe",
            "spoolsv.exe",
            "memcompression",
            "secure system",
            "lsaiso.exe",
            "windefend",
            "msmpeng.exe",
        }:
            processes.PROTECTED_PROCESSES.discard(original_name)


def test_kill_nonexistent_pid_returns_error():
    # A PID that is virtually guaranteed to not exist.
    ok, msg = processes.kill_process(2_000_000_000)
    assert not ok
    assert msg


def test_collect_processes_includes_self():
    procs = processes.collect_processes()
    pids = {p.pid for p in procs}
    assert os.getpid() in pids
    me = next(p for p in procs if p.pid == os.getpid())
    assert me.name
    assert me.memory_mb >= 0


def test_open_file_location_returns_false_for_missing():
    assert processes.open_file_location("") is False
    assert processes.open_file_location("C:/__definitely_not_a_real_path__/x.exe") is False
