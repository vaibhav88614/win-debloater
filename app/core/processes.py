"""Enumerate and control running processes via psutil."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import psutil


# System / critical process names that must not be killed.
PROTECTED_PROCESSES = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "services.exe", "lsass.exe", "winlogon.exe", "fontdrvhost.exe",
    "dwm.exe", "svchost.exe", "explorer.exe", "spoolsv.exe", "memcompression",
    "secure system", "lsaiso.exe", "windefend", "msmpeng.exe",
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


def collect_processes(sample_cpu: bool = False) -> list[ProcessInfo]:
    """Snapshot running processes.

    If ``sample_cpu`` is True, CPU percent is measured over a short interval
    (slower but accurate). Otherwise cumulative values are used.
    """
    procs: list[psutil.Process] = list(psutil.process_iter())

    if sample_cpu:
        for p in procs:
            try:
                p.cpu_percent(None)  # prime
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        psutil.cpu_count()  # no-op to keep import used
        import time
        time.sleep(0.3)

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
