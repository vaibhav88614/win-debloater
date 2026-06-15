"""PowerShell wrapper - exercise the parts that don't require Windows."""
from __future__ import annotations

import sys
import types

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

    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError("powershell.exe")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = ps.run("anything", timeout=5)
    assert not res.ok
    assert "powershell.exe" in res.error.lower()


def test_run_returns_error_on_timeout(monkeypatch):
    import subprocess

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = ps.run("Start-Sleep 5", timeout=1)
    assert not res.ok
    assert "timed out" in res.error.lower()
