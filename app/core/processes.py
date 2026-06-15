"""Enumerate and control running processes via psutil."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import psutil

from app.core import dryrun

# Seconds between the priming and measuring CPU reads. psutil's per-process
# cpu_percent needs two samples; the first always returns 0.0.
_CPU_SAMPLE_INTERVAL = 0.1


# System / critical process names that must not be killed.
PROTECTED_PROCESSES = {
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
}


@dataclass
class ProcessInfo:
    pid: int
    name: str = ""
    exe: str = ""
    username: str = ""
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    create_time: float = 0.0
    cmdline: str = ""
    status: str = ""
    num_connections: int = 0
    ppid: int = 0
    is_protected: bool = False
    # Filled in by the suspicious analyzer.
    suspicion_score: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def is_suspicious(self) -> bool:
        return self.suspicion_score >= 40


def collect_processes() -> list[ProcessInfo]:
    """Snapshot running processes (memory-sorted, descending)."""
    procs: list[psutil.Process] = list(psutil.process_iter())

    # Prime psutil's per-process CPU counters so the sample below is a real
    # utilization figure rather than 0.0 (cpu_percent needs two reads).
    for p in procs:
        try:
            p.cpu_percent(None)
        except Exception:  # noqa: BLE001
            pass
    time.sleep(_CPU_SAMPLE_INTERVAL)

    result: list[ProcessInfo] = []
    ncpu = psutil.cpu_count() or 1
    for p in procs:
        try:
            with p.oneshot():
                name = p.name()
                try:
                    exe = p.exe()
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    exe = ""
                try:
                    username = p.username()
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    username = ""
                try:
                    cpu = p.cpu_percent(None) / ncpu
                except Exception:
                    cpu = 0.0
                try:
                    mem = p.memory_info().rss / (1024 * 1024)
                except Exception:
                    mem = 0.0
                try:
                    cmd = " ".join(p.cmdline())
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    cmd = ""
                try:
                    ctime = p.create_time()
                except Exception:
                    ctime = 0.0
                try:
                    conns = len(p.net_connections(kind="inet"))
                except (psutil.AccessDenied, psutil.NoSuchProcess, Exception):
                    conns = 0
                try:
                    ppid = p.ppid()
                except Exception:
                    ppid = 0
                try:
                    status = p.status()
                except Exception:
                    status = ""

            info = ProcessInfo(
                pid=p.pid,
                name=name,
                exe=exe,
                username=username,
                cpu_percent=round(cpu, 1),
                memory_mb=round(mem, 1),
                create_time=ctime,
                cmdline=cmd,
                status=status,
                num_connections=conns,
                ppid=ppid,
                is_protected=name.lower() in PROTECTED_PROCESSES,
            )
            result.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    result.sort(key=lambda x: x.memory_mb, reverse=True)
    return result


def kill_process(pid: int) -> tuple[bool, str]:
    if dryrun.is_enabled():
        return True, f"dry-run: would terminate PID {pid}"
    try:
        p = psutil.Process(pid)
        if p.name().lower() in PROTECTED_PROCESSES:
            return False, f"'{p.name()}' is a protected system process."
        p.terminate()
        try:
            p.wait(timeout=3)
        except psutil.TimeoutExpired:
            p.kill()
        return True, f"Terminated PID {pid}."
    except psutil.NoSuchProcess:
        return False, "Process no longer exists."
    except psutil.AccessDenied:
        return False, "Access denied (try running as Administrator)."
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def suspend_process(pid: int) -> tuple[bool, str]:
    if dryrun.is_enabled():
        return True, f"dry-run: would suspend PID {pid}"
    try:
        p = psutil.Process(pid)
        if p.name().lower() in PROTECTED_PROCESSES:
            return False, f"'{p.name()}' is a protected system process."
        p.suspend()
        return True, f"Suspended PID {pid}."
    except psutil.NoSuchProcess:
        return False, "Process no longer exists."
    except psutil.AccessDenied:
        return False, "Access denied (try running as Administrator)."
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def resume_process(pid: int) -> tuple[bool, str]:
    if dryrun.is_enabled():
        return True, f"dry-run: would resume PID {pid}"
    try:
        p = psutil.Process(pid)
        p.resume()
        return True, f"Resumed PID {pid}."
    except psutil.NoSuchProcess:
        return False, "Process no longer exists."
    except psutil.AccessDenied:
        return False, "Access denied (try running as Administrator)."
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def open_file_location(exe: str) -> bool:
    if exe and os.path.exists(exe):
        os.startfile(os.path.dirname(exe))  # type: ignore[attr-defined]
        return True
    return False
