"""Tests for Win32/MSI program listing and uninstall."""

from __future__ import annotations

import sys

import pytest

from app.core import dryrun, programs
from app.core import powershell as ps


@pytest.fixture(autouse=True)
def _no_dryrun():
    dryrun.set_enabled(False)
    yield
    dryrun.set_enabled(False)


# ---------------------------------------------------------------------------
# _program_from_item parsing
# ---------------------------------------------------------------------------


def test_program_from_item_msi_extracts_product_code():
    item = {
        "DisplayName": "Windows SDK",
        "DisplayVersion": "10.1.26100.4188",
        "Publisher": "Microsoft Corporation",
        "UninstallString": "MsiExec.exe /I{D2DE764E-178D-DECF-C70F-328C51D35108}",
        "PSChildName": "{D2DE764E-178D-DECF-C70F-328C51D35108}",
        "WindowsInstaller": 1,
        "Hive": "HKLM",
    }
    prog = programs._program_from_item(item)
    assert prog is not None
    assert prog.is_msi is True
    assert prog.product_code == "{D2DE764E-178D-DECF-C70F-328C51D35108}"
    assert prog.name == "Windows SDK"


def test_program_from_item_guid_from_uninstall_string_when_child_not_guid():
    item = {
        "DisplayName": "Some MSI",
        "UninstallString": "MsiExec.exe /X{85D29127-80DA-40D5-1418-743964414E22}",
        "PSChildName": "SomeAppKey",
    }
    prog = programs._program_from_item(item)
    assert prog.is_msi is True
    assert prog.product_code == "{85D29127-80DA-40D5-1418-743964414E22}"


def test_program_from_item_marks_updates():
    item = {
        "DisplayName": "Security Update for Windows",
        "UninstallString": "wusa.exe /uninstall /kb:123",
        "ReleaseType": "Security Update",
    }
    prog = programs._program_from_item(item)
    assert prog.is_update is True


def test_program_from_item_marks_system_component():
    item = {
        "DisplayName": "Hidden Component",
        "UninstallString": "x",
        "SystemComponent": 1,
    }
    prog = programs._program_from_item(item)
    assert prog.is_system_component is True


def test_program_from_item_requires_display_name():
    assert programs._program_from_item({"UninstallString": "x"}) is None


# ---------------------------------------------------------------------------
# list_programs
# ---------------------------------------------------------------------------


def test_list_programs_filters_and_dedups(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("list_programs only queries on Windows")

    data = [
        {"DisplayName": "App A", "UninstallString": "a.exe /uninstall", "DisplayVersion": "1.0"},
        # Duplicate of App A (same name/version/code) -> dropped.
        {"DisplayName": "App A", "UninstallString": "a.exe /uninstall", "DisplayVersion": "1.0"},
        # No uninstall string and not MSI -> dropped.
        {"DisplayName": "Not Removable"},
        {
            "DisplayName": "MSI Thing",
            "UninstallString": "MsiExec.exe /I{AAAAAAAA-1111-2222-3333-444444444444}",
            "WindowsInstaller": 1,
        },
    ]
    monkeypatch.setattr(
        ps, "run_json", lambda script, timeout=120: ps.PSResult(ok=True, returncode=0, data=data)
    )

    result = programs.list_programs()
    names = [p.name for p in result]
    assert names.count("App A") == 1
    assert "MSI Thing" in names
    assert "Not Removable" not in names


def test_list_programs_returns_empty_on_ps_failure(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("list_programs only queries on Windows")
    monkeypatch.setattr(
        ps, "run_json", lambda script, timeout=120: ps.PSResult(ok=False, returncode=1)
    )
    assert programs.list_programs() == []


# ---------------------------------------------------------------------------
# uninstall_program
# ---------------------------------------------------------------------------


def test_uninstall_program_dry_run_makes_no_calls(monkeypatch):
    dryrun.set_enabled(True)
    called = {"n": 0}

    def fake_run(script, *, timeout=900):
        called["n"] += 1
        return ps.PSResult(ok=True, returncode=0)

    monkeypatch.setattr(ps, "run", fake_run)
    prog = programs.Program(name="X", product_code="{G}", is_msi=True)
    res = programs.uninstall_program(prog)
    assert res.ok
    assert dryrun.DRY_RUN_MARKER in res.stdout
    assert called["n"] == 0


def test_uninstall_program_msi_uses_msiexec_silent(monkeypatch):
    captured = {}

    def fake_run(script, *, timeout=900):
        captured["script"] = script
        return ps.PSResult(ok=True, returncode=0)

    monkeypatch.setattr(ps, "run", fake_run)
    prog = programs.Program(
        name="Windows SDK", product_code="{D2DE764E-178D-DECF-C70F-328C51D35108}", is_msi=True
    )
    res = programs.uninstall_program(prog)
    assert res.ok
    assert "msiexec.exe" in captured["script"]
    assert "/x" in captured["script"]
    assert "/qn" in captured["script"]
    assert "{D2DE764E-178D-DECF-C70F-328C51D35108}" in captured["script"]


def test_uninstall_program_reboot_code_is_success(monkeypatch):
    monkeypatch.setattr(
        ps, "run", lambda script, timeout=900: ps.PSResult(ok=False, returncode=3010)
    )
    prog = programs.Program(name="X", product_code="{G}", is_msi=True)
    res = programs.uninstall_program(prog)
    assert res.ok is True
    assert "reboot" in res.stdout.lower()


def test_uninstall_program_prefers_quiet_string_for_non_msi(monkeypatch):
    captured = {}

    def fake_run(script, *, timeout=900):
        captured["script"] = script
        return ps.PSResult(ok=True, returncode=0)

    monkeypatch.setattr(ps, "run", fake_run)
    prog = programs.Program(
        name="App",
        uninstall_string="setup.exe /uninstall",
        quiet_uninstall_string="setup.exe /uninstall /S",
    )
    programs.uninstall_program(prog)
    assert "/S" in captured["script"]


def test_uninstall_program_edge_routes_to_appx(monkeypatch):
    from app.core import appx

    called = {"edge": False}

    def fake_edge(*, deprovision=True):
        called["edge"] = True
        return ps.PSResult(ok=True, returncode=0, stdout="edge removed")

    monkeypatch.setattr(appx, "remove_edge_chromium", fake_edge)
    prog = programs.Program(name="Microsoft Edge", product_code="{X}", is_msi=True)
    res = programs.uninstall_program(prog)
    assert called["edge"] is True
    assert res.ok


def test_uninstall_program_no_command_fails():
    prog = programs.Program(name="Broken")
    res = programs.uninstall_program(prog)
    assert res.ok is False
    assert "No uninstall command" in res.error


# ---------------------------------------------------------------------------
# Program groups
# ---------------------------------------------------------------------------


def test_load_groups_includes_windows_sdk():
    groups = programs.load_groups()
    ids = {g.id for g in groups}
    assert "windows_sdk" in ids


def test_match_group_windows_sdk_matches_microsoft_sdk_components():
    group = next(g for g in programs.load_groups() if g.id == "windows_sdk")
    items = [
        programs.Program(name="Windows SDK Desktop Libs x64", publisher="Microsoft Corporation"),
        programs.Program(name="Universal CRT Extension SDK", publisher="Microsoft Corporation"),
        programs.Program(name="SDK ARM64 Additions", publisher="Microsoft Corporation"),
        # Wrong publisher -> excluded even though the name contains SDK.
        programs.Program(name="Acme SDK Tools", publisher="Acme Inc"),
        # Not an SDK -> excluded.
        programs.Program(name="Microsoft Edge", publisher="Microsoft Corporation"),
    ]
    matched = programs.match_group(group, items)
    names = {p.name for p in matched}
    assert "Windows SDK Desktop Libs x64" in names
    assert "Universal CRT Extension SDK" in names
    assert "SDK ARM64 Additions" in names
    assert "Acme SDK Tools" not in names
    assert "Microsoft Edge" not in names


def test_match_group_vc_redist():
    group = next(g for g in programs.load_groups() if g.id == "vc_redist")
    items = [
        programs.Program(
            name="Microsoft Visual C++ 2015-2022 Redistributable (x64)",
            publisher="Microsoft Corporation",
        ),
        programs.Program(name="Windows SDK", publisher="Microsoft Corporation"),
    ]
    matched = programs.match_group(group, items)
    assert len(matched) == 1
    assert "Visual C++" in matched[0].name
