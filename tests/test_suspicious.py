"""Suspicion-scoring heuristics (no PowerShell dependency)."""

from __future__ import annotations

from app.core import suspicious
from app.core.processes import ProcessInfo


def _proc(**kw) -> ProcessInfo:
    base = dict(
        pid=1234,
        name="x.exe",
        exe="",
        username="u",
        cpu_percent=0.0,
        memory_mb=10.0,
        create_time=0,
        cmdline="",
        status="running",
        num_connections=0,
        ppid=1,
        is_protected=False,
    )
    base.update(kw)
    return ProcessInfo(**base)


def test_looks_random_flags_high_entropy_names():
    assert suspicious._looks_random("a1b2c3d4e5.exe") is True
    assert suspicious._looks_random("notepad.exe") is False
    assert suspicious._looks_random("svchost.exe") is False


def test_looks_random_spares_short_names_with_digits():
    # Short legitimate tool names should not trip the digit heuristic.
    assert suspicious._looks_random("7zG.exe") is False
    assert suspicious._looks_random("ms-teams.exe") is False


def test_looks_random_spares_long_names_with_vowel_clusters():
    # Vowel-heavy long names should not be flagged.
    assert suspicious._looks_random("microsoft-update-helper.exe") is False
    assert suspicious._looks_random("BraveBrowserSetup.exe") is False


def test_looks_random_flags_digit_heavy_names():
    # > 40% digits over a long basename is suspicious.
    assert suspicious._looks_random("9023871-23x.exe") is True


def test_looks_random_flags_consonant_heavy_high_entropy():
    # No vowels at all + long basename = looks random.
    assert suspicious._looks_random("xkqzwbtprmvchsgld.exe") is True


def test_in_suspicious_dir_detects_temp_paths():
    assert suspicious._in_suspicious_dir(r"C:\Users\me\AppData\Local\Temp\x.exe")
    assert suspicious._in_suspicious_dir(r"C:\Users\me\Downloads\foo.exe")
    assert not suspicious._in_suspicious_dir(r"C:\Windows\System32\notepad.exe")


def test_analyze_flags_system_binary_in_wrong_location(monkeypatch):
    # Stub PowerShell-backed helpers so analyze() never shells out.
    monkeypatch.setattr(suspicious, "get_autostart_targets", lambda: set())
    monkeypatch.setattr(suspicious, "get_signatures", lambda paths: {})

    procs = [
        _proc(name="svchost.exe", exe=r"C:\Users\me\Downloads\svchost.exe"),
        _proc(name="notepad.exe", exe=r"C:\Windows\System32\notepad.exe", pid=2),
    ]
    out = suspicious.analyze(procs, verify_signatures=False)

    bad = next(p for p in out if p.pid == 1234)
    good = next(p for p in out if p.pid == 2)
    assert bad.suspicion_score >= 45
    assert any("system binary" in r.lower() for r in bad.reasons)
    assert good.suspicion_score == 0


def test_analyze_skips_protected_processes(monkeypatch):
    monkeypatch.setattr(suspicious, "get_autostart_targets", lambda: set())
    monkeypatch.setattr(suspicious, "get_signatures", lambda paths: {})

    procs = [_proc(is_protected=True, name="svchost.exe", exe=r"C:\Users\me\Downloads\svchost.exe")]
    out = suspicious.analyze(procs, verify_signatures=False)
    assert out[0].suspicion_score == 0
    assert out[0].reasons == []


def test_analyze_flags_invalid_signature(monkeypatch):
    monkeypatch.setattr(suspicious, "get_autostart_targets", lambda: set())
    monkeypatch.setattr(
        suspicious,
        "get_signatures",
        lambda paths: {r"c:\users\me\appdata\local\app\bad.exe": "NotSigned"},
    )

    procs = [_proc(name="bad.exe", exe=r"C:\Users\me\AppData\Local\App\bad.exe")]
    out = suspicious.analyze(procs, verify_signatures=True)
    assert any("signature" in r.lower() for r in out[0].reasons)
    assert out[0].suspicion_score >= 35


def test_analyze_no_exe_with_network_scores_higher(monkeypatch):
    monkeypatch.setattr(suspicious, "get_autostart_targets", lambda: set())
    monkeypatch.setattr(suspicious, "get_signatures", lambda paths: {})

    with_net = _proc(name="x.exe", exe="", num_connections=3, pid=10)
    no_net = _proc(name="y.exe", exe="", num_connections=0, pid=11)
    out = suspicious.analyze([with_net, no_net], verify_signatures=False)
    a = next(p for p in out if p.pid == 10)
    b = next(p for p in out if p.pid == 11)
    assert a.suspicion_score > b.suspicion_score
