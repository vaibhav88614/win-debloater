"""PowerShell wrapper - exercise the parts that don't require Windows."""

from __future__ import annotations

import sys

import pytest

from app.core import powershell as ps


def test_psresult_items_normalizes_to_list_of_dicts():
    assert ps.PSResult(ok=True, returncode=0, data=None).items == []
    assert ps.PSResult(ok=True, returncode=0, data={"a": 1}).items == [{"a": 1}]
    assert ps.PSResult(ok=True, returncode=0, data=[{"a": 1}, "ignored", {"b": 2}]).items == [
        {"a": 1},
        {"b": 2},
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell only available on Windows")
def test_run_executes_powershell_and_captures_stdout():
    res = ps.run("Write-Output 'hello-world'", timeout=30)
    assert res.ok
    assert "hello-world" in res.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell only available on Windows")
def test_run_json_parses_array_output():
    res = ps.run_json("[pscustomobject]@{a=1; b='two'}", timeout=30)
    assert res.ok
    assert res.items and res.items[0]["a"] == 1
    assert res.items[0]["b"] == "two"


def test_run_returns_error_when_powershell_missing(monkeypatch):
    """Simulate powershell.exe not being on PATH."""
    import subprocess

    def fake_popen(*_args, **_kwargs):
        raise FileNotFoundError("powershell.exe")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    res = ps.run("anything", timeout=5)
    assert not res.ok
    assert "powershell.exe" in res.error.lower()


def test_run_returns_error_on_timeout(monkeypatch):
    import subprocess

    class _TimeoutProc:
        pid = 9999
        returncode = None

        def communicate(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="powershell", timeout=timeout)
            return ("", "")

        def kill(self):
            self.returncode = -1

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _TimeoutProc())
    res = ps.run("Start-Sleep 5", timeout=1)
    assert not res.ok
    assert "timed out" in res.error.lower()


# ---------------------------------------------------------------------------
# ps_quote (A1)
# ---------------------------------------------------------------------------


def test_ps_quote_wraps_plain_value():
    assert ps.ps_quote("DiagTrack") == "'DiagTrack'"


def test_ps_quote_doubles_embedded_apostrophes():
    assert ps.ps_quote("Bing's News") == "'Bing''s News'"


def test_ps_quote_handles_empty_and_none():
    assert ps.ps_quote("") == "''"
    assert ps.ps_quote(None) == "''"  # type: ignore[arg-type]


def test_ps_quote_coerces_non_string():
    assert ps.ps_quote(42) == "'42'"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# UTF-8 / script wrapping (A2, A3)
# ---------------------------------------------------------------------------


def _captured_call(monkeypatch):
    """Return a list where the first item is the (args, kwargs) of subprocess.Popen."""
    import subprocess

    captured: list = []

    class _Proc:
        returncode = 0
        pid = 4321

        def communicate(self, timeout=None):
            return ("", "")

        def kill(self):
            pass

    def fake_popen(*args, **kwargs):
        captured.append((args, kwargs))
        return _Proc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return captured


def test_run_prepends_utf8_prelude_and_uses_utf8_decoding(monkeypatch):
    captured = _captured_call(monkeypatch)
    ps.run("Write-Output 'hi'", timeout=5)

    (args, kwargs) = captured[0]
    # The PowerShell command line is the first positional argument (a list).
    cmd = args[0]
    # The script body is the last element after "-Command".
    assert "-Command" in cmd
    script_body = cmd[cmd.index("-Command") + 1]
    assert script_body.startswith("[Console]::OutputEncoding=[Text.Encoding]::UTF8;")
    assert "Write-Output 'hi'" in script_body

    # Decoding settings.
    assert kwargs.get("encoding") == "utf-8"
    assert kwargs.get("errors") == "replace"


def test_run_json_wraps_in_script_block(monkeypatch):
    captured = _captured_call(monkeypatch)
    ps.run_json("Get-Foo", timeout=5)

    cmd = captured[0][0][0]
    script_body = cmd[cmd.index("-Command") + 1]
    # New wrapper uses an explicit script block.
    assert "& { Get-Foo }" in script_body
    assert "ConvertTo-Json" in script_body
    # Still sets stop-on-error and silences progress.
    assert "$ErrorActionPreference='Stop'" in script_body
    assert "$ProgressPreference='SilentlyContinue'" in script_body


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell only available on Windows")
def test_run_json_handles_empty_pipeline():
    """A pipeline that emits nothing should not raise, items==[]."""
    res = ps.run_json("@() | Where-Object { $false }", timeout=30)
    assert res.ok
    assert res.items == []


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell only available on Windows")
def test_run_json_handles_single_object():
    res = ps.run_json("[pscustomobject]@{x=1}", timeout=30)
    assert res.ok
    assert res.items == [{"x": 1}]


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell only available on Windows")
def test_run_preserves_non_ascii_output():
    res = ps.run("Write-Output 'caf\u00e9'", timeout=30)
    assert res.ok
    assert "caf\u00e9" in res.stdout


# ---------------------------------------------------------------------------
# Cancellation (Phase 3)
# ---------------------------------------------------------------------------


def test_cancel_active_with_no_procs_returns_zero():
    assert ps.cancel_active() == 0


def test_cancel_active_kills_registered_procs():
    class _FakeProc:
        def __init__(self):
            self.pid = 13579
            self.killed = False

        def kill(self):
            self.killed = True

    fp = _FakeProc()
    ps._register(fp)
    try:
        n = ps.cancel_active()
        assert n == 1
        assert fp.killed is True
        assert fp.pid in ps._cancelled_pids
    finally:
        ps._unregister(fp)
        ps._cancelled_pids.discard(fp.pid)
