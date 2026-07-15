"""Catalog loading and pattern matching for AppX packages."""

from __future__ import annotations

import pytest

from app.core import appx


@pytest.fixture(autouse=True)
def _clear_appx_cache():
    """Ensure the module-level list cache never leaks between tests."""
    appx.invalidate_cache()
    yield
    appx.invalidate_cache()


def test_load_catalog_returns_packages():
    catalog = appx.load_catalog()
    assert isinstance(catalog, list)
    assert len(catalog) > 10
    # Every entry has an id and a name.
    for entry in catalog:
        assert "id" in entry and entry["id"]
        assert "name" in entry


def test_match_catalog_exact_id():
    catalog = appx.load_catalog()
    entry = appx._match_catalog("Microsoft.BingNews", catalog)
    assert entry is not None
    assert entry["id"] == "Microsoft.BingNews"


def test_match_catalog_wildcard_pattern():
    catalog = appx.load_catalog()
    # "*.CandyCrush" exists in the catalog and should match a real-world name.
    entry = appx._match_catalog("king.com.CandyCrush", catalog)
    assert entry is not None
    # king.com.* should match this too; either rule wins, but a match must exist.


def test_match_catalog_unknown_returns_none():
    catalog = appx.load_catalog()
    assert appx._match_catalog("NotAnAppEverInstalled.Xyz", catalog) is None


def test_appx_package_display_name_prefers_friendly():
    pkg = appx.AppxPackage(name="Microsoft.BingNews", full_name="...", friendly_name="News")
    assert pkg.display_name == "News"
    pkg2 = appx.AppxPackage(name="Microsoft.BingNews", full_name="...")
    assert pkg2.display_name == "Microsoft.BingNews"


# ---------------------------------------------------------------------------
# remove_package (A1 quoting + A13 sequential)
# ---------------------------------------------------------------------------


def _fake_run_factory(calls, results):
    """Build a fake ps.run that records each script and returns scripted results."""

    def fake_run(script, *, timeout=60, **_kwargs):
        # Accept and ignore any extra kwargs (e.g. label) so this fake stays
        # compatible with future signature additions to ps.run.
        calls.append(script)
        # Use a result for each call in order, last result if we run out.
        idx = min(len(calls) - 1, len(results) - 1)
        return results[idx]

    return fake_run


def test_remove_package_quotes_names_with_apostrophes(monkeypatch):
    from app.core import powershell as ps

    calls: list[str] = []
    ok = ps.PSResult(ok=True, returncode=0)
    monkeypatch.setattr(ps, "run", _fake_run_factory(calls, [ok]))

    pkg = appx.AppxPackage(name="App'Name", full_name="App'Name_1.0_x64__abc")
    appx.remove_package(pkg, all_users=True, deprovision=True)

    joined = "\n".join(calls)
    # Apostrophes doubled, value wrapped in single quotes.
    assert "'App''Name'" in joined
    assert "'App''Name_1.0_x64__abc'" in joined


def test_remove_package_runs_steps_sequentially(monkeypatch):
    """Each removal step should be its own ps.run call (not one big script)."""
    from app.core import powershell as ps

    calls: list[str] = []
    ok = ps.PSResult(ok=True, returncode=0)
    monkeypatch.setattr(ps, "run", _fake_run_factory(calls, [ok]))

    pkg = appx.AppxPackage(name="Foo.App", full_name="Foo.App_1.0_x64__xyz")
    res = appx.remove_package(pkg)

    assert res.ok
    # remove-by-name + remove-by-fullname + deprovision = 3 calls.
    assert len(calls) == 3
    assert any("Get-AppxPackage" in c for c in calls)
    assert any("Remove-AppxPackage" in c and "-Package" in c for c in calls)
    assert any("Remove-AppxProvisionedPackage" in c for c in calls)


def test_remove_package_unlocks_nonremovable_first(monkeypatch):
    from app.core import powershell as ps

    calls: list[str] = []
    ok = ps.PSResult(ok=True, returncode=0)
    monkeypatch.setattr(ps, "run", _fake_run_factory(calls, [ok]))

    pkg = appx.AppxPackage(
        name="Locked.App", full_name="Locked.App_1_x64__h", is_non_removable=True
    )
    appx.remove_package(pkg)

    # First call is the registry unlock.
    assert "AppxAllUserStore" in calls[0]
    assert "NonRemovable" in calls[0]


def test_remove_package_overall_ok_if_any_removal_succeeds(monkeypatch):
    """Aggregation rule: ok if any removal step succeeded (unlock step ignored)."""
    from app.core import powershell as ps

    ok = ps.PSResult(ok=True, returncode=0, stdout="removed")
    fail = ps.PSResult(ok=False, returncode=1, stderr="not found")
    # Sequence: name-remove fails, fullname-remove succeeds, deprovision fails.
    calls: list[str] = []
    results = [fail, ok, fail]

    def fake_run(script, *, timeout=60, **_kwargs):
        calls.append(script)
        return results[len(calls) - 1]

    monkeypatch.setattr(ps, "run", fake_run)

    pkg = appx.AppxPackage(name="X", full_name="X_1_x64__h")
    res = appx.remove_package(pkg)
    assert res.ok is True
    assert "removed" in res.stdout


def test_remove_package_overall_fails_if_all_removals_fail(monkeypatch):
    from app.core import powershell as ps

    fail = ps.PSResult(ok=False, returncode=1, stderr="no")
    calls: list[str] = []
    monkeypatch.setattr(ps, "run", _fake_run_factory(calls, [fail]))

    pkg = appx.AppxPackage(name="X", full_name="X_1_x64__h")
    res = appx.remove_package(pkg)
    assert res.ok is False
    assert res.error


# ---------------------------------------------------------------------------
# Chromium Edge special-case removal
# ---------------------------------------------------------------------------


def test_is_edge_chromium_detects_by_name_and_catalog_id():
    assert appx.is_edge_chromium(
        appx.AppxPackage(name="Microsoft.MicrosoftEdge.Stable", full_name="")
    )
    assert appx.is_edge_chromium(
        appx.AppxPackage(name="Whatever", full_name="", catalog_id="Microsoft.MicrosoftEdge.Stable")
    )
    assert not appx.is_edge_chromium(appx.AppxPackage(name="Microsoft.BingNews", full_name=""))


def test_remove_package_routes_edge_to_setup_uninstaller(monkeypatch):
    called = {"edge": 0, "appx": 0}

    def fake_edge(*, deprovision=True):
        called["edge"] += 1
        from app.core import powershell as ps

        return ps.PSResult(ok=True, returncode=0, stdout="edge removed")

    from app.core import powershell as ps

    monkeypatch.setattr(appx, "remove_edge_chromium", fake_edge)
    monkeypatch.setattr(
        ps,
        "run",
        lambda *a, **k: (
            called.__setitem__("appx", called["appx"] + 1) or ps.PSResult(ok=True, returncode=0)
        ),
    )

    pkg = appx.AppxPackage(name="Microsoft.MicrosoftEdge.Stable", full_name="x_1_x64__y")
    res = appx.remove_package(pkg)
    assert res.ok
    assert called["edge"] == 1
    # The generic AppX removal steps must NOT run for Edge.
    assert called["appx"] == 0


def test_remove_edge_chromium_dry_run(monkeypatch):
    from app.core import dryrun

    dryrun.set_enabled(True)
    try:
        res = appx.remove_edge_chromium()
        assert res.ok
        assert dryrun.DRY_RUN_MARKER in res.stdout
    finally:
        dryrun.set_enabled(False)


def test_remove_edge_chromium_runs_setup_and_deprovisions(monkeypatch):
    from app.core import powershell as ps

    scripts: list[str] = []

    def fake_run(script, *, timeout=120, **_kwargs):
        scripts.append(script)
        return ps.PSResult(ok=True, returncode=0, stdout="setup.exe (149) exit=0")

    monkeypatch.setattr(ps, "run", fake_run)
    res = appx.remove_edge_chromium(deprovision=True)
    assert res.ok
    joined = "\n".join(scripts)
    # Combined across all staged calls we expect policy set, processes
    # killed, setup.exe invoked, and the provisioned entry removed.
    assert "AllowUninstall" in joined
    assert "Stop-Process" in joined
    assert "setup.exe" in joined
    assert "--force-uninstall" in joined
    assert "Remove-AppxProvisionedPackage" in joined


def test_remove_edge_chromium_writes_correct_policy_and_kills_processes(monkeypatch):
    """Regression: the old script wrote AllowUninstall to Policies\\EdgeUpdate,
    which Edge's setup.exe ignores on Windows 11 22H2+ (causing exit 93).
    The fixed script must target EdgeUpdateDev (both native + WOW6432Node)
    and kill Edge/EdgeUpdate/WebView2 processes before invoking setup.exe.
    """
    from app.core import powershell as ps

    scripts: list[str] = []

    def fake_run(script, *, timeout=120, **_kwargs):
        scripts.append(script)
        return ps.PSResult(ok=True, returncode=0, stdout="setup.exe (149) exit=0")

    monkeypatch.setattr(ps, "run", fake_run)
    appx.remove_edge_chromium(deprovision=False)

    # Phase 1 script must set AllowUninstall under the real key + WOW6432Node.
    policy_script = scripts[0]
    assert "SOFTWARE\\Microsoft\\EdgeUpdateDev" in policy_script
    assert "WOW6432Node\\Microsoft\\EdgeUpdateDev" in policy_script
    assert "AllowUninstall" in policy_script

    # Phase 2 script must kill the processes that would otherwise hold
    # Edge files open.
    kill_script = scripts[1]
    assert "Stop-Process" in kill_script
    for proc in ("msedge", "MicrosoftEdgeUpdate", "msedgewebview2"):
        assert proc in kill_script

    # Phase 3 script must actually invoke setup.exe.
    setup_script = scripts[2]
    assert "Start-Process" in setup_script
    assert "--force-uninstall" in setup_script


def test_remove_edge_chromium_reports_exit_93_clearly(monkeypatch):
    """Exit code 93 ('Uninstall was blocked') must be translated into a
    human-readable error rather than surfaced as a bare integer.
    """
    from app.core import powershell as ps

    def fake_run(script, *, timeout=120, **_kwargs):
        # The setup.exe phase returns 93; policy/kill/deprovision succeed.
        if "Start-Process" in script and "setup.exe" in script.lower():
            return ps.PSResult(
                ok=False,
                returncode=appx.EDGE_EXIT_BLOCKED,
                stdout="setup.exe (149) exit=93",
            )
        return ps.PSResult(ok=True, returncode=0)

    monkeypatch.setattr(ps, "run", fake_run)
    res = appx.remove_edge_chromium(deprovision=True)
    assert res.ok is False
    assert "exit 93" in res.error
    assert "blocked" in res.error.lower()
    # Users need actionable guidance, not just "it failed".
    assert "Settings" in res.error or "EEA" in res.error


# ---------------------------------------------------------------------------
# OS-protected AppX packages (0x80070032 / ERROR_NOT_SUPPORTED)
# ---------------------------------------------------------------------------


def test_is_os_protected_recognises_error_not_supported():
    assert appx._is_os_protected("Deployment failed with error 0x80070032. See...")
    assert appx._is_os_protected("HRESULT: 0X80070032")  # case-insensitive
    assert appx._is_os_protected("failed 0x80073CFA (part of Windows)")
    assert not appx._is_os_protected("")
    assert not appx._is_os_protected("Some other error 0x12345678")


def test_remove_package_translates_os_protected_failure(monkeypatch):
    """When Remove-AppxPackage reports 0x80070032 (ERROR_NOT_SUPPORTED) —
    e.g. for Microsoft.Windows.ContentDeliveryManager on Windows 11 24H2+ —
    the aggregated error must explain *why* removal is blocked instead of
    dumping the raw COMException traceback.
    """
    from app.core import powershell as ps

    def fake_run(script, *, timeout=60, **_kwargs):
        return ps.PSResult(
            ok=False,
            returncode=1,
            stderr=(
                "Remove-AppxPackage : Removal failed. Please contact your "
                "software vendor. Deployment Remove operation ... failed with "
                "error 0x80070032."
            ),
        )

    monkeypatch.setattr(ps, "run", fake_run)
    pkg = appx.AppxPackage(
        name="Microsoft.Windows.ContentDeliveryManager",
        full_name="Microsoft.Windows.ContentDeliveryManager_10.0.26100.1_neutral_neutral_cw5n1h2txyewy",
        is_non_removable=True,
    )
    res = appx.remove_package(pkg)
    assert res.ok is False
    assert "0x80070032" in res.error
    assert "OS component" in res.error
    assert "Microsoft.Windows.ContentDeliveryManager" in res.error


def test_remove_package_generic_failure_keeps_stderr(monkeypatch):
    """Non-OS-protected failures should still surface the underlying stderr
    (so we don't lose real error text for unrelated problems)."""
    from app.core import powershell as ps

    def fake_run(script, *, timeout=60, **_kwargs):
        return ps.PSResult(
            ok=False,
            returncode=1,
            stderr="Some unexpected error: package file is corrupted.",
        )

    monkeypatch.setattr(ps, "run", fake_run)
    pkg = appx.AppxPackage(name="Foo.App", full_name="Foo.App_1_x64__h")
    res = appx.remove_package(pkg)
    assert res.ok is False
    assert "corrupted" in res.error


# ---------------------------------------------------------------------------
# Provisioned-package merging (A7)
# ---------------------------------------------------------------------------


def test_name_from_package_name_strips_versioned_suffix():
    assert (
        appx._name_from_package_name("Microsoft.BingNews_4.55.1.0_x64__8wekyb3d8bbwe")
        == "Microsoft.BingNews"
    )


def test_name_from_package_name_passes_through_short_names():
    assert appx._name_from_package_name("Microsoft.BingNews") == "Microsoft.BingNews"
    assert appx._name_from_package_name("") == ""


def test_list_installed_merges_provisioned_by_canonical_name(monkeypatch):
    """A provisioned-only package merges by the stripped Name, not DisplayName."""
    from app.core import powershell as ps

    # First call: installed packages (none for "Foo.App").
    installed_result = ps.PSResult(ok=True, returncode=0, data=[])
    # Second call: provisioned list with a full PackageName.
    provisioned_result = ps.PSResult(
        ok=True,
        returncode=0,
        data=[
            {
                "DisplayName": "Foo.App",
                "PackageName": "Foo.App_2.0.0.0_x64__abc123",
            }
        ],
    )
    results = iter([installed_result, provisioned_result])
    monkeypatch.setattr(ps, "run_json", lambda script, timeout=180: next(results))

    pkgs = appx.list_installed()
    foo = [p for p in pkgs if p.name == "Foo.App"]
    assert len(foo) == 1
    assert foo[0].is_provisioned is True
    assert foo[0].full_name == "Foo.App_2.0.0.0_x64__abc123"


def test_list_installed_provisioned_attaches_to_installed_entry(monkeypatch):
    """Installed entry merges with the provisioned entry (no duplicate row)."""
    from app.core import powershell as ps

    installed_result = ps.PSResult(
        ok=True,
        returncode=0,
        data=[
            {
                "Name": "Foo.App",
                "PackageFullName": "Foo.App_1.0.0.0_x64__abc",
                "Publisher": "X",
                "Version": "1.0",
                "InstallLocation": "C:/X",
                "NonRemovable": False,
            }
        ],
    )
    provisioned_result = ps.PSResult(
        ok=True,
        returncode=0,
        data=[
            {
                "DisplayName": "Foo.App",
                "PackageName": "Foo.App_2.0.0.0_x64__abc123",
            }
        ],
    )
    results = iter([installed_result, provisioned_result])
    monkeypatch.setattr(ps, "run_json", lambda script, timeout=180: next(results))

    pkgs = appx.list_installed()
    foos = [p for p in pkgs if p.name == "Foo.App"]
    assert len(foos) == 1  # merged, not duplicated
    assert foos[0].is_provisioned is True
    # Existing full_name from installed list is preserved.
    assert foos[0].full_name == "Foo.App_1.0.0.0_x64__abc"


# ---------------------------------------------------------------------------
# TTL cache (Phase 3)
# ---------------------------------------------------------------------------


def test_list_installed_caches_until_invalidated(monkeypatch):
    import sys

    if sys.platform != "win32":
        pytest.skip("list_installed only queries on Windows")
    from app.core import powershell as ps

    calls = {"n": 0}

    def fake(script, timeout=180):
        calls["n"] += 1
        return ps.PSResult(ok=True, returncode=0, data=[])

    monkeypatch.setattr(ps, "run_json", fake)
    appx.invalidate_cache()

    first = appx.list_installed()
    n1 = calls["n"]
    assert n1 >= 1

    # Second call within TTL is served from cache (no new PS calls).
    second = appx.list_installed()
    assert calls["n"] == n1
    assert second is first

    # force=True bypasses the cache.
    appx.list_installed(force=True)
    assert calls["n"] > n1

    # invalidate_cache also forces a refresh.
    prev = calls["n"]
    appx.invalidate_cache()
    appx.list_installed()
    assert calls["n"] > prev


# ---------------------------------------------------------------------------
# User catalog overlay (Phase 4)
# ---------------------------------------------------------------------------


def test_user_overlay_merges_and_overrides(tmp_path, monkeypatch):
    import json as _json

    overlay = tmp_path / "bloatware.user.json"
    overlay.write_text(
        _json.dumps(
            {
                "version": 3,
                "packages": [
                    {"id": "Microsoft.BingNews", "name": "OVERRIDDEN NAME"},
                    {"id": "Vendor.NewApp", "name": "Brand New", "category": "Third-Party"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(appx, "user_catalog_path", lambda: overlay)

    cat = appx.load_catalog()
    by_id = {e["id"].lower(): e for e in cat}
    assert by_id["microsoft.bingnews"]["name"] == "OVERRIDDEN NAME"
    assert "vendor.newapp" in by_id


def test_user_overlay_absent_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(appx, "user_catalog_path", lambda: tmp_path / "nope.json")
    cat = appx.load_catalog()
    assert isinstance(cat, list) and len(cat) > 10
