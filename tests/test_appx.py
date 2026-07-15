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


def test_remove_edge_chromium_runs_all_phases_in_order(monkeypatch):
    """Regression: the old script wrote AllowUninstall to Policies\\EdgeUpdate,
    which Edge's setup.exe ignores on Windows 11 22H2+ (causing exit 93).
    The fixed sequence must be: policy -> kill -> JSON patch (A) -> setup.exe
    -> deprovision, with policy/kill/setup targeting the correct locations.
    """
    from app.core import powershell as ps

    scripts: list[tuple[str, str]] = []

    def fake_run(script, *, timeout=120, label=None, **_kwargs):
        scripts.append((label or "", script))
        return ps.PSResult(ok=True, returncode=0, stdout="setup.exe (149) exit=0")

    monkeypatch.setattr(ps, "run", fake_run)
    appx.remove_edge_chromium(deprovision=False)

    # Assert on the labels because they're stable across script text changes.
    labels = [lbl for lbl, _ in scripts]
    assert labels[0] == "edge:policy"
    assert labels[1] == "edge:kill"
    assert labels[2] == "edge:policy-json-patch"
    assert labels[3] == "edge:setup"
    # deprovision=False, so no edge:deprovision. setup.exe returned 0, so no
    # force-delete fallback either.
    assert "edge:deprovision" not in labels
    assert "edge:force-delete" not in labels

    # Phase 1 (policy): AllowUninstall in the correct keys.
    policy_script = scripts[0][1]
    assert "SOFTWARE\\Microsoft\\EdgeUpdateDev" in policy_script
    assert "WOW6432Node\\Microsoft\\EdgeUpdateDev" in policy_script
    assert "AllowUninstall" in policy_script

    # Phase 2 (kill): kills the four blocking process names.
    kill_script = scripts[1][1]
    assert "Stop-Process" in kill_script
    for proc in ("msedge", "MicrosoftEdgeUpdate", "msedgewebview2"):
        assert proc in kill_script

    # Phase 3 (JSON patch): targets IntegratedServicesRegionPolicySet.json.
    patch_script = scripts[2][1]
    assert "IntegratedServicesRegionPolicySet.json" in patch_script
    assert "takeown" in patch_script
    assert "defaultState" in patch_script

    # Phase 4 (setup.exe): actual force-uninstall invocation.
    setup_script = scripts[3][1]
    assert "Start-Process" in setup_script
    assert "--force-uninstall" in setup_script


def test_remove_edge_chromium_falls_through_to_force_delete_on_93(monkeypatch):
    """When setup.exe returns 93, we chain the force-delete fallback and
    report overall success (the browser IS gone from disk) with an error
    string that says explicitly what happened.
    """
    from app.core import powershell as ps

    seen_labels: list[str] = []

    def fake_run(script, *, timeout=120, label=None, **_kwargs):
        seen_labels.append(label or "")
        if label == "edge:setup":
            return ps.PSResult(
                ok=False,
                returncode=appx.EDGE_EXIT_BLOCKED,
                stdout="setup.exe (149) exit=93",
            )
        if label == "edge:force-delete":
            return ps.PSResult(ok=True, returncode=0, stdout="edge:force-delete deleted stuff")
        return ps.PSResult(ok=True, returncode=0)

    monkeypatch.setattr(ps, "run", fake_run)
    res = appx.remove_edge_chromium(deprovision=True)

    # We should have hit both setup.exe AND the force-delete fallback.
    assert "edge:setup" in seen_labels
    assert "edge:force-delete" in seen_labels
    # Overall result is now success because the fallback removed Edge.
    assert res.ok is True
    # But we still explain what happened in the error/status message so the
    # user knows setup.exe was refused before the fallback took over.
    assert "93" in res.error
    assert "force-delete" in res.error
    assert "DoNotUpdateToEdgeWithChromium" in res.error


def test_remove_edge_chromium_reports_exit_93_when_force_delete_disabled(monkeypatch):
    """With ``force_delete_on_block=False`` we skip the nuke fallback and
    surface the actionable exit-93 error (used e.g. from tests or for
    users who want a chance to review before deletion)."""
    from app.core import powershell as ps

    def fake_run(script, *, timeout=120, label=None, **_kwargs):
        if label == "edge:setup":
            return ps.PSResult(
                ok=False,
                returncode=appx.EDGE_EXIT_BLOCKED,
                stdout="setup.exe (149) exit=93",
            )
        # Fail loudly if the force-delete path is hit despite being disabled.
        assert label != "edge:force-delete", "force-delete must not run when disabled"
        return ps.PSResult(ok=True, returncode=0)

    monkeypatch.setattr(ps, "run", fake_run)
    res = appx.remove_edge_chromium(deprovision=True, force_delete_on_block=False)
    assert res.ok is False
    assert "93" in res.error
    assert "blocked" in res.error.lower()
    assert "Settings" in res.error or "EEA" in res.error


# ---------------------------------------------------------------------------
# Phase A: IntegratedServicesRegionPolicySet.json patcher
# ---------------------------------------------------------------------------


def test_patch_edge_uninstall_policy_uses_takeown_and_targets_json(monkeypatch):
    from app.core import powershell as ps

    captured: list[tuple[str, str]] = []

    def fake_run(script, *, timeout=60, label=None, **_kwargs):
        captured.append((label or "", script))
        return ps.PSResult(ok=True, returncode=0, stdout="policy-json: patched 1 policy/policies")

    monkeypatch.setattr(ps, "run", fake_run)
    res = appx.patch_edge_uninstall_policy()
    assert res.ok

    label, script = captured[0]
    assert label == "edge:policy-json-patch"
    assert "IntegratedServicesRegionPolicySet.json" in script
    assert "takeown.exe" in script
    assert "icacls.exe" in script
    assert "ConvertFrom-Json" in script
    assert "defaultState" in script
    # Backup path lives in the user data dir (isolated by conftest).
    assert "IntegratedServicesRegionPolicySet.backup.json" in script


def test_patch_edge_uninstall_policy_dry_run(monkeypatch):
    from app.core import dryrun

    dryrun.set_enabled(True)
    try:
        res = appx.patch_edge_uninstall_policy()
        assert res.ok
        assert dryrun.DRY_RUN_MARKER in res.stdout
    finally:
        dryrun.set_enabled(False)


def test_restore_edge_uninstall_policy_copies_backup(monkeypatch):
    from app.core import powershell as ps

    captured: list[tuple[str, str]] = []

    def fake_run(script, *, timeout=60, label=None, **_kwargs):
        captured.append((label or "", script))
        return ps.PSResult(ok=True, returncode=0)

    monkeypatch.setattr(ps, "run", fake_run)
    appx.restore_edge_uninstall_policy()
    assert captured[0][0] == "edge:policy-json-restore"
    assert "Copy-Item" in captured[0][1]


# ---------------------------------------------------------------------------
# Phase C: force_delete_edge
# ---------------------------------------------------------------------------


def test_force_delete_edge_covers_all_categories(monkeypatch):
    """force_delete_edge must touch files, registry, shortcuts, tasks,
    services, and set the DoNotUpdateToEdgeWithChromium block."""
    from app.core import powershell as ps

    captured: list[tuple[str, str]] = []

    def fake_run(script, *, timeout=60, label=None, **_kwargs):
        captured.append((label or "", script))
        return ps.PSResult(ok=True, returncode=0, stdout="edge:force-delete done")

    monkeypatch.setattr(ps, "run", fake_run)
    res = appx.force_delete_edge()
    assert res.ok
    assert captured[0][0] == "edge:force-delete"
    body = captured[0][1]
    # Files.
    assert "Microsoft\\Edge" in body
    assert "takeown.exe" in body
    assert "Remove-Item" in body
    # Registry.
    assert "SOFTWARE\\Clients\\StartMenuInternet\\Microsoft Edge" in body
    assert "Uninstall\\Microsoft Edge" in body
    # Scheduled tasks.
    assert "MicrosoftEdgeUpdate*" in body
    assert "Unregister-ScheduledTask" in body
    # Services.
    assert "edgeupdate" in body
    assert "Set-Service" in body
    # Prevent-reinstall block.
    assert "DoNotUpdateToEdgeWithChromium" in body


def test_force_delete_edge_dry_run():
    from app.core import dryrun

    dryrun.set_enabled(True)
    try:
        res = appx.force_delete_edge()
        assert res.ok
        assert dryrun.DRY_RUN_MARKER in res.stdout
    finally:
        dryrun.set_enabled(False)


# ---------------------------------------------------------------------------
# Generic force_delete_appx (for OS-protected AppX like Edge DevTools)
# ---------------------------------------------------------------------------


def test_family_name_from_full_name_extracts_publisher_hash():
    assert (
        appx._family_name_from_full_name("Microsoft.Foo_1.0.0.0_x64__8wekyb3d8bbwe")
        == "Microsoft.Foo_8wekyb3d8bbwe"
    )
    assert appx._family_name_from_full_name("") == ""
    assert appx._family_name_from_full_name("NoUnderscore") == ""


def test_force_delete_appx_takes_ownership_and_deletes(monkeypatch):
    from app.core import powershell as ps

    captured: list[tuple[str, str]] = []

    def fake_run(script, *, timeout=60, label=None, **_kwargs):
        captured.append((label or "", script))
        return ps.PSResult(ok=True, returncode=0, stdout="force-delete done")

    monkeypatch.setattr(ps, "run", fake_run)

    pkg = appx.AppxPackage(
        name="Microsoft.MicrosoftEdgeDevToolsClient",
        full_name="Microsoft.MicrosoftEdgeDevToolsClient_1000.25128.1000.0_neutral_neutral_8wekyb3d8bbwe",
        install_location=r"C:\Program Files\WindowsApps\Microsoft.MicrosoftEdgeDevToolsClient_1000.25128.1000.0_neutral_neutral_8wekyb3d8bbwe",
    )
    res = appx.force_delete_appx(pkg)
    assert res.ok

    body = captured[0][1]
    label = captured[0][0]
    assert label.startswith("force-delete-appx")
    assert "takeown.exe" in body
    assert "Remove-Item" in body
    # Both the versioned key and the deprovisioned marker are targeted.
    assert "AppxAllUserStore\\Applications\\" in body
    assert "AppxAllUserStore\\Deprovisioned\\" in body
    # Package identifiers are quoted and interpolated into the script.
    assert "Microsoft.MicrosoftEdgeDevToolsClient" in body


def test_force_delete_appx_refuses_when_no_location():
    """We refuse to run if we have nothing to point takeown/Remove-Item at,
    to avoid nuking the wrong folder."""
    pkg = appx.AppxPackage(name="Unknown.Pkg", full_name="", install_location="")
    res = appx.force_delete_appx(pkg)
    assert res.ok is False
    assert "InstallLocation" in res.error or "PackageFullName" in res.error


def test_force_delete_appx_dry_run():
    from app.core import dryrun

    dryrun.set_enabled(True)
    try:
        pkg = appx.AppxPackage(
            name="Foo.App", full_name="Foo.App_1_x64__h", install_location="C:/x"
        )
        res = appx.force_delete_appx(pkg)
        assert res.ok
        assert dryrun.DRY_RUN_MARKER in res.stdout
    finally:
        dryrun.set_enabled(False)


# ---------------------------------------------------------------------------
# remove_package fallback into force_delete_appx on OS-protected failure
# ---------------------------------------------------------------------------


def test_remove_package_falls_through_to_force_delete_on_0x80070032(monkeypatch):
    """When Remove-AppxPackage fails with the OS-protected HRESULT we now
    attempt force_delete_appx before giving up, and the overall result
    reflects the fallback outcome."""
    from app.core import powershell as ps

    labels: list[str] = []

    def fake_run(script, *, timeout=60, label=None, **_kwargs):
        labels.append(label or "")
        # unlock succeeds; both real removals fail with the OS-protected
        # HRESULT; deprovision succeeds; force-delete succeeds.
        if label and label.startswith("unlock"):
            return ps.PSResult(ok=True, returncode=0)
        if label and label.startswith("remove-by-"):
            return ps.PSResult(
                ok=False,
                returncode=1,
                stderr="Deployment Remove operation failed with error 0x80070032.",
            )
        if label and label.startswith("deprovision"):
            return ps.PSResult(ok=True, returncode=0)
        if label and label.startswith("force-delete-appx"):
            return ps.PSResult(ok=True, returncode=0, stdout="force-delete deleted: C:\\...\\Foo")
        return ps.PSResult(ok=True, returncode=0)

    monkeypatch.setattr(ps, "run", fake_run)

    pkg = appx.AppxPackage(
        name="Microsoft.MicrosoftEdgeDevToolsClient",
        full_name="Microsoft.MicrosoftEdgeDevToolsClient_1_neutral__abc",
        install_location=r"C:\Program Files\WindowsApps\Microsoft.MicrosoftEdgeDevToolsClient_1_neutral__abc",
        is_non_removable=True,
    )
    res = appx.remove_package(pkg)

    # force-delete was invoked as the fallback.
    assert any(lbl.startswith("force-delete-appx") for lbl in labels), labels
    # Overall reported as success because the fallback succeeded.
    assert res.ok is True
    assert "force-delete" in res.stdout


def test_remove_package_reports_partial_when_only_deprovision_succeeds(monkeypatch):
    """If Remove-AppxPackage fails with a NON-OS-protected error and only
    deprovision succeeds, we report PARTIAL rather than a misleading OK.
    (OS-protected errors instead trigger the force-delete fallback.)"""
    from app.core import powershell as ps

    def fake_run(script, *, timeout=60, label=None, **_kwargs):
        if label and label.startswith("remove-by-"):
            # Some generic non-OS-protected transient error.
            return ps.PSResult(ok=False, returncode=1, stderr="Some transient failure.")
        return ps.PSResult(ok=True, returncode=0)

    monkeypatch.setattr(ps, "run", fake_run)
    pkg = appx.AppxPackage(name="Foo.App", full_name="Foo.App_1_x64__h")
    res = appx.remove_package(pkg)
    # OK overall because deprovision succeeded, but the error message must
    # tell the user honestly that the installed copy is still there.
    assert res.ok is True
    assert "Partial" in res.error
    assert "deprovisioned" in res.error


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
