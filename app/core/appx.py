"""List, remove, and restore Windows Store (AppX) packages."""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from fnmatch import fnmatch

from app.core import dryrun
from app.core import powershell as ps
from app.core.paths import resource_path, user_data_dir

# Matches the version/arch/publisher-hash suffix on AppX PackageName values,
# e.g. ``Microsoft.BingNews_4.55.1.0_x64__8wekyb3d8bbwe``.
_PKG_FULLNAME_SUFFIX = re.compile(r"_\d+(?:\.\d+)+_[a-zA-Z0-9]+(?:_[a-zA-Z0-9]*)?_[a-z0-9]+$")

# Schema version expected in ``app/core/data/bloatware.json``.
CATALOG_VERSION = 3

# Catalog id / package name of Chromium Microsoft Edge. Edge cannot be removed
# with ``Remove-AppxPackage`` (Windows silently blocks it); it must be removed
# with its bundled ``setup.exe --uninstall`` installer instead.
EDGE_STABLE_ID = "Microsoft.MicrosoftEdge.Stable"

# HRESULT strings that indicate Windows itself refuses to remove a package
# (it is a protected/OS-integrated component). Seen in ``stderr`` from
# ``Remove-AppxPackage`` on Windows 11 24H2+ for packages such as
# ``Microsoft.Windows.ContentDeliveryManager`` and
# ``Microsoft.MicrosoftEdgeDevToolsClient``.
_ERROR_NOT_SUPPORTED = "0x80070032"  # ERROR_NOT_SUPPORTED
_ERROR_ACCESS_DENIED = "0x80073cfa"  # ERROR_INSTALL_OPEN_PACKAGE_FAILED ("part of Windows")
_OS_PROTECTED_MARKERS = (_ERROR_NOT_SUPPORTED, _ERROR_ACCESS_DENIED)

# Edge Chromium ``setup.exe`` exit code emitted when Windows blocks the
# uninstall (``Uninstall was blocked for this product: 93`` in the Edge
# installer log). See https://github.com/ChrisTitusTech/winutil/issues/2672.
EDGE_EXIT_BLOCKED = 93

# Short-lived cache for ``list_installed`` so re-entering the tab is instant.
# Mutations (remove/restore) call ``invalidate_cache`` to force a refresh.
_LIST_CACHE_TTL = 10.0
_list_cache: dict = {"time": 0.0, "data": None, "key": None}


def invalidate_cache() -> None:
    """Drop any cached ``list_installed`` result."""
    _list_cache["data"] = None


def _name_from_package_name(package_name: str) -> str:
    """Derive the canonical AppX ``Name`` from a ``PackageName``/``PackageFullName``.

    >>> _name_from_package_name('Microsoft.BingNews_4.55.1.0_x64__8wekyb3d8bbwe')
    'Microsoft.BingNews'
    >>> _name_from_package_name('Microsoft.BingNews')
    'Microsoft.BingNews'
    """
    if not package_name:
        return ""
    return _PKG_FULLNAME_SUFFIX.sub("", package_name)


@dataclass
class AppxPackage:
    """A single installed AppX package mapped to its catalog metadata."""

    name: str  # PackageName, e.g. Microsoft.BingNews
    full_name: str  # PackageFullName (versioned)
    publisher: str = ""
    version: str = ""
    install_location: str = ""
    is_provisioned: bool = False  # Present in the provisioned (per-image) store
    is_non_removable: bool = False  # Windows marks this NonRemovable in the AppX DB
    # Catalog enrichment
    friendly_name: str = ""
    category: str = "Other"
    safe: bool = True
    description: str = ""
    catalog_id: str = ""
    removal_note: str = ""  # Extra guidance shown in the UI tooltip

    @property
    def display_name(self) -> str:
        return self.friendly_name or self.name


def load_catalog() -> list[dict]:
    """Load the curated bloatware catalog from bundled JSON.

    Validates the top-level ``version`` and the shape of each entry. Invalid
    rows are dropped and a warning is logged so the UI keeps working even with
    a partially malformed catalog.
    """
    from app.core.applog import get_logger

    log = get_logger()
    path = resource_path("app", "core", "data", "bloatware.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to load bloatware catalog: %s", exc)
        return []

    version = data.get("version")
    if version != CATALOG_VERSION:
        log.warning(
            "Bloatware catalog version mismatch: expected %s, got %r",
            CATALOG_VERSION,
            version,
        )

    raw = data.get("packages", [])
    if not isinstance(raw, list):
        log.warning("Catalog 'packages' is not a list; ignoring.")
        return []

    valid: list[dict] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            log.warning("Catalog entry #%d is not an object; skipped.", idx)
            continue
        if not entry.get("id") or not entry.get("name"):
            log.warning("Catalog entry #%d missing 'id' or 'name'; skipped: %r", idx, entry)
            continue
        valid.append(entry)

    # Merge an optional user overlay so users can add OEM/vendor apps without
    # editing the bundled catalog. Overlay entries override bundled ones by id.
    overlay = _load_user_overlay()
    if overlay:
        by_id = {e["id"].lower(): e for e in valid}
        added = 0
        for entry in overlay:
            key = entry["id"].lower()
            if key not in by_id:
                added += 1
            by_id[key] = entry
        valid = list(by_id.values())
        log.info("Applied user catalog overlay: %d entr(y/ies), %d new.", len(overlay), added)
    return valid


def user_catalog_path():
    """Path to the optional user catalog overlay file."""
    from app.core.paths import user_data_dir

    return user_data_dir() / "bloatware.user.json"


def _load_user_overlay() -> list[dict]:
    """Load and validate the optional ``bloatware.user.json`` overlay."""
    from app.core.applog import get_logger

    log = get_logger()
    path = user_catalog_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to load user catalog overlay: %s", exc)
        return []

    raw = data.get("packages", []) if isinstance(data, dict) else []
    if not isinstance(raw, list):
        log.warning("User overlay 'packages' is not a list; ignoring.")
        return []

    valid: list[dict] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        if not entry.get("id") or not entry.get("name"):
            log.warning("User overlay entry #%d missing 'id' or 'name'; skipped.", idx)
            continue
        valid.append(entry)
    return valid


def _match_catalog(package_name: str, catalog: list[dict]) -> dict | None:
    """Match an installed package name against catalog patterns (exact, then wildcard)."""
    low = package_name.lower()
    for entry in catalog:
        pattern = entry.get("id", "")
        if not pattern:
            continue
        if pattern.lower() == low:
            return entry
    for entry in catalog:
        pattern = entry.get("id", "")
        if "*" in pattern and fnmatch(low, pattern.lower()):
            return entry
    return None


def list_installed(include_all_users: bool = True, *, force: bool = False) -> list[AppxPackage]:
    """List *all* AppX packages (including NonRemovable), enriched with catalog data.

    ``is_non_removable`` is set on packages Windows has flagged as protected;
    the caller (UI) decides whether to display/allow actions on them.

    Results are cached for a few seconds so re-entering the tab is instant;
    pass ``force=True`` (or call :func:`invalidate_cache`) to bypass it.
    """
    if sys.platform != "win32":
        return []

    now = time.monotonic()
    if (
        not force
        and _list_cache["data"] is not None
        and _list_cache["key"] == include_all_users
        and (now - _list_cache["time"]) < _LIST_CACHE_TTL
    ):
        return _list_cache["data"]

    catalog = load_catalog()
    packages: dict[str, AppxPackage] = {}

    scope = "-AllUsers" if include_all_users else ""
    # Fetch ALL packages — including NonRemovable — so nothing is hidden.
    user_script = (
        f"Get-AppxPackage {scope} | "
        "Select-Object Name, PackageFullName, Publisher, Version, InstallLocation, NonRemovable"
    )
    res = ps.run_json(user_script, timeout=180)
    if res.ok:
        for item in res.items:
            name = item.get("Name") or ""
            if not name:
                continue
            packages[name] = AppxPackage(
                name=name,
                full_name=item.get("PackageFullName") or "",
                publisher=item.get("Publisher") or "",
                version=str(item.get("Version") or ""),
                install_location=item.get("InstallLocation") or "",
                is_non_removable=bool(item.get("NonRemovable")),
            )

    # Provisioned packages (apply to new user profiles / feature updates).
    prov_script = "Get-AppxProvisionedPackage -Online | Select-Object DisplayName, PackageName"
    prov = ps.run_json(prov_script, timeout=180)
    if prov.ok:
        for item in prov.items:
            disp = item.get("DisplayName") or ""
            pkgname = item.get("PackageName") or ""
            # Prefer deriving the canonical Name from PackageName so we merge
            # cleanly with the installed list (whose key is also "Name").
            name = _name_from_package_name(pkgname) or disp
            if not name:
                continue
            if name in packages:
                packages[name].is_provisioned = True
                if pkgname and not packages[name].full_name:
                    packages[name].full_name = pkgname
            else:
                packages[name] = AppxPackage(
                    name=name,
                    full_name=pkgname,
                    is_provisioned=True,
                )

    # Enrich with catalog metadata.
    result = []
    for pkg in packages.values():
        entry = _match_catalog(pkg.name, catalog)
        if entry:
            pkg.friendly_name = entry.get("name", "")
            pkg.category = entry.get("category", "Other")
            pkg.safe = bool(entry.get("safe", True))
            pkg.description = entry.get("description", "")
            pkg.catalog_id = entry.get("id", "")
            pkg.removal_note = entry.get("removal_note", "")
            # If marked force_required in catalog, override the is_non_removable flag
            # (some packages are mis-labelled, or the flag is lifted by elevation).
            if entry.get("force_required"):
                pkg.is_non_removable = True
        else:
            pkg.friendly_name = ""
            pkg.category = "Other (not in catalog)"
            # Unknown packages: safe=False so they only appear in Advanced mode.
            pkg.safe = False

        result.append(pkg)

    result.sort(key=lambda p: (p.category, p.display_name.lower()))
    _list_cache.update(time=time.monotonic(), data=result, key=include_all_users)
    return result


def is_edge_chromium(pkg: AppxPackage) -> bool:
    """True when ``pkg`` is the Chromium Microsoft Edge browser package."""
    ident = (pkg.catalog_id or pkg.name or "").lower()
    return ident == EDGE_STABLE_ID.lower()


def _log():
    """Local helper; the applog module is imported lazily to avoid cycles."""
    from app.core.applog import get_logger

    return get_logger()


# ----------------------------------------------------------------------------
# Edge uninstall Phase A: patch IntegratedServicesRegionPolicySet.json
# ----------------------------------------------------------------------------
#
# On Windows 11 22H2+ Microsoft ships a JSON at
# ``%SystemRoot%\System32\IntegratedServicesRegionPolicySet.json`` that
# gates a handful of features on the machine's region — including whether
# Edge can be uninstalled. In the EEA the "Edge uninstall" policy is
# ``enabled`` by default; elsewhere it is ``disabled``. When it is
# ``enabled``, Settings > Apps > Microsoft Edge grows a working "Uninstall"
# button and ``setup.exe --force-uninstall`` sometimes stops returning 93.
#
# We take ownership of the file, back it up into user_data_dir(), rewrite
# every policy whose ``$comment`` looks like an Edge-uninstall gate so its
# ``defaultState`` becomes ``"enabled"``, and drop the region condition.
# This is fully reversible from the backup.
#
# Reference: this is the same trick published by Rafael Rivera (@WithinRafael)
# and used by well-known third-party tools (e.g.
# https://github.com/Chiragsd13/microsoft-edge-uninstaller).

_EDGE_POLICY_JSON = r"C:\Windows\System32\IntegratedServicesRegionPolicySet.json"


def _edge_policy_backup_path():
    return user_data_dir() / "IntegratedServicesRegionPolicySet.backup.json"


def patch_edge_uninstall_policy() -> ps.PSResult:
    """Phase A: patch the region policy JSON so Windows allows Edge uninstall.

    Best-effort — we swallow failures because Phase C (force-delete) is the
    real fallback. Returns the ``PSResult`` for logging.
    """
    if dryrun.is_enabled():
        return dryrun.dry_result(
            f"would patch {_EDGE_POLICY_JSON} to enable Edge uninstall globally"
        )

    backup = str(_edge_policy_backup_path()).replace("'", "''")
    json_path_q = _EDGE_POLICY_JSON.replace("'", "''")
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$json_path = '{json_path_q}'
$backup    = '{backup}'

if (-not (Test-Path -LiteralPath $json_path)) {{
    Write-Output "policy-json: file not present on this build; skipping"
    exit 0
}}

# 1) Backup once. Never overwrite an existing backup so we always retain
#    the truly original file, even across multiple patch attempts.
if (-not (Test-Path -LiteralPath $backup)) {{
    try {{
        Copy-Item -LiteralPath $json_path -Destination $backup -Force -ErrorAction Stop
        Write-Output "policy-json: backup written to $backup"
    }} catch {{
        Write-Output "policy-json: backup FAILED ($($_.Exception.Message))"
    }}
}}

# 2) Take ownership + grant admins full control. Owned by TrustedInstaller
#    by default so we cannot write to it without this.
& takeown.exe /f $json_path 2>&1 | Out-Null
& icacls.exe $json_path /grant "*S-1-5-32-544:(F)" 2>&1 | Out-Null

# 3) Parse, mutate any Edge-uninstall policy, write back.
$patched = 0
try {{
    $doc = Get-Content -LiteralPath $json_path -Raw | ConvertFrom-Json
    foreach ($p in @($doc.policies)) {{
        $comment = ''
        if ($p.PSObject.Properties.Name -contains '$comment') {{ $comment = [string]$p.'$comment' }}
        # Any policy whose comment mentions Edge + uninstall / EEA is the
        # gate we want. GUIDs shift between builds; matching on the comment
        # is more durable.
        if ($comment -match '(?i)edge' -and
            ($comment -match '(?i)uninstall' -or $comment -match '(?i)EEA' -or $comment -match '(?i)region'))
        {{
            $p.defaultState = 'enabled'
            if ($p.PSObject.Properties.Name -contains 'conditions') {{
                # Wipe geographic gating so the policy applies everywhere.
                $p.conditions = @{{}}
            }}
            $patched++
        }}
    }}
    if ($patched -gt 0) {{
        $doc | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $json_path -Encoding UTF8
        Write-Output "policy-json: patched $patched policy/policies"
    }} else {{
        Write-Output "policy-json: no matching Edge-uninstall policy found; nothing to patch"
    }}
}} catch {{
    Write-Output "policy-json: parse/patch FAILED ($($_.Exception.Message))"
    exit 1
}}
exit 0
"""
    return ps.run(script, timeout=60, label="edge:policy-json-patch")


def restore_edge_uninstall_policy() -> ps.PSResult:
    """Restore ``IntegratedServicesRegionPolicySet.json`` from the backup made
    by :func:`patch_edge_uninstall_policy`. No-op if no backup exists.
    """
    if dryrun.is_enabled():
        return dryrun.dry_result("would restore IntegratedServicesRegionPolicySet.json from backup")
    backup = str(_edge_policy_backup_path()).replace("'", "''")
    json_path_q = _EDGE_POLICY_JSON.replace("'", "''")
    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$json_path = '{json_path_q}'
$backup    = '{backup}'
if (-not (Test-Path -LiteralPath $backup)) {{
    Write-Output 'policy-json restore: no backup found; nothing to do'
    exit 0
}}
& takeown.exe /f $json_path 2>&1 | Out-Null
& icacls.exe $json_path /grant "*S-1-5-32-544:(F)" 2>&1 | Out-Null
Copy-Item -LiteralPath $backup -Destination $json_path -Force
Write-Output "policy-json restore: restored from $backup"
"""
    return ps.run(script, timeout=30, label="edge:policy-json-restore")


# ----------------------------------------------------------------------------
# Edge uninstall Phase C: force-delete Edge files, registry, tasks, services
# ----------------------------------------------------------------------------


def force_delete_edge() -> ps.PSResult:
    """Delete Edge's install location, registry entries, shortcuts, tasks,
    services, and set ``DoNotUpdateToEdgeWithChromium=1`` so Windows Update
    doesn't bring it back on the next check.

    This is the last-resort fallback for when ``setup.exe --force-uninstall``
    refuses (exit 93) on modern Windows builds. Not a "clean" uninstall —
    a few dead registry stubs may linger — but Edge stops working and the
    Store entry is already deprovisioned by the caller.
    """
    if dryrun.is_enabled():
        return dryrun.dry_result("would force-delete Microsoft Edge (files + registry + tasks)")

    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$actions = @()

# 1) Kill anything Edge/EdgeUpdate/WebView that might hold files open.
$procs = @('msedge', 'MicrosoftEdgeUpdate', 'msedgewebview2', 'identity_helper', 'msedge_notification_client_setup')
foreach ($name in $procs) {
    Get-Process -Name $name -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
}
$actions += 'killed running Edge processes'

# 2) Take ownership + delete every Edge install folder (system + user level).
$targets = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge'),
    (Join-Path $env:ProgramFiles       'Microsoft\Edge'),
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\EdgeUpdate'),
    (Join-Path $env:ProgramFiles       'Microsoft\EdgeUpdate'),
    (Join-Path $env:ProgramData         'Microsoft\EdgeUpdate'),
    (Join-Path $env:LOCALAPPDATA        'Microsoft\Edge'),
    (Join-Path $env:LOCALAPPDATA        'Microsoft\EdgeUpdate')
)
foreach ($t in $targets) {
    if (Test-Path -LiteralPath $t) {
        & takeown.exe /f $t /r /d Y 2>&1 | Out-Null
        & icacls.exe $t /grant "*S-1-5-32-544:(F)" /t /c /q 2>&1 | Out-Null
        Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $t) {
            $actions += "delete FAILED: $t (some files still in use)"
        } else {
            $actions += "deleted: $t"
        }
    }
}

# 3) Registry cleanup. Kill the entries that Explorer / Settings / other
#    installers look at when deciding whether Edge is present.
$regKeys = @(
    'HKLM:\SOFTWARE\Clients\StartMenuInternet\Microsoft Edge',
    'HKLM:\SOFTWARE\WOW6432Node\Clients\StartMenuInternet\Microsoft Edge',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Microsoft Edge',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Microsoft Edge',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Microsoft Edge Update',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Microsoft Edge Update',
    'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\ClientState',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\ClientState'
)
foreach ($k in $regKeys) {
    if (Test-Path -LiteralPath $k) {
        Remove-Item -LiteralPath $k -Recurse -Force -ErrorAction SilentlyContinue
        $actions += "removed key: $k"
    }
}

# 4) Delete Start Menu shortcuts.
$shortcuts = @(
    (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\Microsoft Edge.lnk'),
    (Join-Path $env:PUBLIC     'Desktop\Microsoft Edge.lnk')
)
foreach ($s in $shortcuts) {
    if (Test-Path -LiteralPath $s) {
        Remove-Item -LiteralPath $s -Force -ErrorAction SilentlyContinue
        $actions += "removed shortcut: $s"
    }
}

# 5) Unregister Edge auto-update scheduled tasks. The task names all start
#    with "MicrosoftEdgeUpdate" but the exact suffix varies by version.
Get-ScheduledTask -TaskName 'MicrosoftEdgeUpdate*' -ErrorAction SilentlyContinue |
    ForEach-Object {
        try {
            Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false -ErrorAction Stop
            $actions += ("unregistered task: " + $_.TaskName)
        } catch {
            $actions += ("unregister task FAILED: " + $_.TaskName)
        }
    }

# 6) Disable Edge auto-update services so Windows Update won't relaunch them.
foreach ($svc in @('edgeupdate', 'edgeupdatem', 'MicrosoftEdgeElevationService')) {
    if (Get-Service -Name $svc -ErrorAction SilentlyContinue) {
        Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue
        Set-Service  -Name $svc -StartupType Disabled -ErrorAction SilentlyContinue
        $actions += "disabled service: $svc"
    }
}

# 7) Block Windows Update from silently pushing Edge back the next time it
#    runs a client-config check.
$blockKey = 'HKLM:\SOFTWARE\Microsoft\EdgeUpdate'
if (-not (Test-Path -LiteralPath $blockKey)) {
    New-Item -Path $blockKey -Force | Out-Null
}
New-ItemProperty -Path $blockKey -Name 'DoNotUpdateToEdgeWithChromium' `
    -PropertyType DWord -Value 1 -Force | Out-Null
$actions += 'set DoNotUpdateToEdgeWithChromium = 1'

$actions | ForEach-Object { Write-Output ("edge:force-delete " + $_) }
"""
    return ps.run(script, timeout=180, label="edge:force-delete")


# ----------------------------------------------------------------------------
# Generic AppX force-remove for OS-protected packages (Edge DevTools, etc.)
# ----------------------------------------------------------------------------


def force_delete_appx(pkg: AppxPackage) -> ps.PSResult:
    """Force-remove an AppX package that Windows refuses to uninstall.

    Strategy: take ownership of the WindowsApps install location, kill
    running processes that map into it, delete the folder, then scrub the
    ``AppxAllUserStore\\Applications\\{FullName}`` and ``Deprovisioned``
    registry entries so Windows stops re-registering it on user logon.

    Only meaningful for packages under ``C:\\Program Files\\WindowsApps`` —
    for Chromium Edge use :func:`force_delete_edge` instead.
    """
    if dryrun.is_enabled():
        return dryrun.dry_result(f"would force-delete AppX '{pkg.name}' via WindowsApps takeown")

    if not pkg.install_location and not pkg.full_name:
        return ps.PSResult(
            ok=False,
            returncode=1,
            error=(
                f"Cannot force-delete '{pkg.name}': neither InstallLocation nor "
                "PackageFullName is known. Refresh the app list and retry."
            ),
        )

    install_q = ps.ps_quote(pkg.install_location or "")
    fullname_q = ps.ps_quote(pkg.full_name or "")
    familyname_q = ps.ps_quote(_family_name_from_full_name(pkg.full_name))

    script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$install  = {install_q}
$fullname = {fullname_q}
$family   = {familyname_q}
$actions = @()

# 1) If InstallLocation is empty (package not resolvable), synthesise it.
if (-not $install -and $fullname) {{
    $candidate = Join-Path $env:ProgramFiles ("WindowsApps\" + $fullname)
    if (Test-Path -LiteralPath $candidate) {{ $install = $candidate }}
}}

# 2) Kill any process running from the install folder. Otherwise deletion
#    fails with "file in use" for the exe.
if ($install) {{
    Get-Process -ErrorAction SilentlyContinue | Where-Object {{
        $_.Path -and $_.Path.StartsWith($install, [StringComparison]::OrdinalIgnoreCase)
    }} | ForEach-Object {{
        try {{ Stop-Process -Id $_.Id -Force -ErrorAction Stop }} catch {{ }}
        $actions += ("killed process: " + $_.ProcessName + " (pid " + $_.Id + ")")
    }}
}}

# 3) Take ownership + full control of the install folder (WindowsApps is
#    owned by TrustedInstaller by default) and delete it recursively.
if ($install -and (Test-Path -LiteralPath $install)) {{
    & takeown.exe /f $install /r /d Y 2>&1 | Out-Null
    & icacls.exe $install /grant "*S-1-5-32-544:(F)" /t /c /q 2>&1 | Out-Null
    Remove-Item -LiteralPath $install -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $install) {{
        $actions += ("delete FAILED: " + $install + " (some files still in use)")
    }} else {{
        $actions += ("deleted: " + $install)
    }}
}} else {{
    $actions += "install location not present on disk"
}}

# 4) Scrub the AppX registration so Windows doesn't try to re-register it
#    on the next user logon.
if ($fullname) {{
    $appKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Appx\AppxAllUserStore\Applications\$fullname"
    if (Test-Path -LiteralPath $appKey) {{
        # NonRemovable lock already cleared by _unlock step, but re-clear
        # here in case this path is invoked standalone.
        Remove-ItemProperty -LiteralPath $appKey -Name 'NonRemovable' -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $appKey -Recurse -Force -ErrorAction SilentlyContinue
        $actions += ("removed reg: " + $appKey)
    }}
    $pkgKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Appx\AppxAllUserStore\Packages\$fullname"
    if (Test-Path -LiteralPath $pkgKey) {{
        Remove-Item -LiteralPath $pkgKey -Recurse -Force -ErrorAction SilentlyContinue
        $actions += ("removed reg: " + $pkgKey)
    }}
}}
if ($family) {{
    $deprov = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Appx\AppxAllUserStore\Deprovisioned\$family"
    # Presence here tells Windows "don't reinstall for new users" — we
    # WANT this key to exist, not delete it. Only delete if the caller
    # asked to fully undo. For now we just ensure it exists.
    if (-not (Test-Path -LiteralPath $deprov)) {{
        New-Item -Path $deprov -Force | Out-Null
        $actions += ("marked deprovisioned: " + $family)
    }}
}}

if ($actions.Count -eq 0) {{
    Write-Output "force-delete: nothing to do (package not on disk / already gone)"
}} else {{
    $actions | ForEach-Object {{ Write-Output ("force-delete " + $_) }}
}}
"""
    return ps.run(script, timeout=180, label=f"force-delete-appx {pkg.name}")


def _family_name_from_full_name(full_name: str) -> str:
    """Derive PackageFamilyName from PackageFullName.

    ``PackageFamilyName`` is ``{Name}_{PublisherHash}`` — the last underscore
    segment of the full name, appended to the leading name segment. A trivial
    string operation, but it's easier to read this way:

    >>> _family_name_from_full_name('Microsoft.Foo_1.0.0.0_x64__8wekyb3d8bbwe')
    'Microsoft.Foo_8wekyb3d8bbwe'
    """
    if not full_name:
        return ""
    parts = full_name.split("_")
    if len(parts) < 2:
        return ""
    return f"{parts[0]}_{parts[-1]}"


def remove_edge_chromium(
    *,
    deprovision: bool = True,
    timeout: int = 300,
    force_delete_on_block: bool = True,
) -> ps.PSResult:
    """Uninstall Chromium Microsoft Edge.

    ``Remove-AppxPackage`` does not actually uninstall Edge — Windows blocks it.
    On Windows 11 22H2+ even ``setup.exe --force-uninstall`` is rejected with
    exit code 93 ("Uninstall was blocked for this product") unless invoked
    from an OS-approved caller such as ``SystemSettings.exe``.

    We therefore chain three strategies:

    1. **Policy setup + process kill.** Set ``AllowUninstall`` under
       ``EdgeUpdateDev`` (and its ``WOW6432Node`` mirror — the one
       ``setup.exe`` actually reads) and kill any Edge/EdgeUpdate/WebView2
       process that would hold Edge files open. Fast, always safe.
    2. **Region-policy JSON patch (A).** Take ownership of
       ``%SystemRoot%\\System32\\IntegratedServicesRegionPolicySet.json``,
       back it up to the user data dir, and rewrite the Edge-uninstall
       policy to be globally ``enabled``. On some builds this alone flips
       setup.exe's decision to allow removal.
    3. **``setup.exe --force-uninstall``.** The supported path when it works.
       If it returns exit 93 despite (1) and (2), fall through to…
    4. **Force delete (C).** Kill everything Edge, take ownership of and
       recursively delete the install folders + user data, scrub the
       registered launcher / Uninstall / EdgeUpdate registry keys,
       unregister the ``MicrosoftEdgeUpdate*`` scheduled tasks, disable
       the ``edgeupdate*`` services, and set
       ``DoNotUpdateToEdgeWithChromium = 1`` so Windows Update won't
       silently reinstate Edge on the next client-config poll.
    5. **Deprovision.** Removes the provisioned AppX entry so freshly
       created user profiles don't pick Edge back up on first logon.

    Pass ``force_delete_on_block=False`` to skip step (4) if setup.exe still
    fails — useful for tests or if the user wants to try only the "clean"
    paths and manually review before nuking files.

    Requires Administrator. Reversible pieces (JSON patch, service state)
    can be undone via :func:`restore_edge_uninstall_policy` and re-enabling
    the services in the Services tab.
    """
    if dryrun.is_enabled():
        return dryrun.dry_result("would uninstall Microsoft Edge (Chromium) via setup.exe")

    log = _log()
    log.info(
        "Edge uninstall: starting (deprovision=%s, force_delete_on_block=%s, timeout=%ss)",
        deprovision,
        force_delete_on_block,
        timeout,
    )
    invalidate_cache()

    # ------------------------------------------------------------------
    # Phase 1: AllowUninstall policy where setup.exe actually reads it.
    # ------------------------------------------------------------------
    policy_script = r"""
$ErrorActionPreference = 'SilentlyContinue'
# The Policies\Microsoft\EdgeUpdate key used to be checked but is ignored on
# Windows 11 22H2+; EdgeUpdateDev is the one setup.exe actually reads.
$policyKeys = @(
    'HKLM:\SOFTWARE\Microsoft\EdgeUpdateDev',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdateDev',
    'HKLM:\SOFTWARE\Policies\Microsoft\EdgeUpdate'
)
foreach ($k in $policyKeys) {
    if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }
    New-ItemProperty -Path $k -Name 'AllowUninstall' -PropertyType DWord `
        -Value 1 -Force | Out-Null
}
Write-Output 'edge: AllowUninstall policy set'
"""
    log.info("Edge uninstall [1/5]: setting AllowUninstall policy…")
    ps.run(policy_script, timeout=30, label="edge:policy")

    # ------------------------------------------------------------------
    # Phase 2: Close anything that would hold Edge files/policies open.
    # ------------------------------------------------------------------
    kill_script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$procs = @('msedge', 'MicrosoftEdgeUpdate', 'msedgewebview2', 'identity_helper')
$killed = @()
foreach ($name in $procs) {
    $found = Get-Process -Name $name -ErrorAction SilentlyContinue
    if ($found) {
        $killed += ("{0} x{1}" -f $name, $found.Count)
        $found | Stop-Process -Force -ErrorAction SilentlyContinue
    }
}
if ($killed.Count -gt 0) {
    Write-Output ("edge: killed processes: " + ($killed -join ', '))
} else {
    Write-Output 'edge: no Edge processes were running'
}
"""
    log.info("Edge uninstall [2/5]: killing Edge/EdgeUpdate/WebView2 processes…")
    ps.run(kill_script, timeout=30, label="edge:kill")

    # ------------------------------------------------------------------
    # Phase 3 (A): patch the region-policy JSON. Best-effort.
    # ------------------------------------------------------------------
    log.info(
        "Edge uninstall [3/5]: patching IntegratedServicesRegionPolicySet.json (EEA workaround)…"
    )
    patch_res = patch_edge_uninstall_policy()
    log.info("Edge uninstall [3/5]: JSON patch stdout=%r", patch_res.stdout.strip())

    # ------------------------------------------------------------------
    # Phase 4: setup.exe --force-uninstall under every installed version.
    # ------------------------------------------------------------------
    setup_script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$roots = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application')
)
$ran = $false
$worst = 0
foreach ($root in $roots) {
    if (-not (Test-Path $root)) { continue }
    Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d+\.' } | ForEach-Object {
            $setup = Join-Path $_.FullName 'Installer\setup.exe'
            if (Test-Path $setup) {
                $ran = $true
                Write-Output ("edge: invoking setup.exe for version {0}" -f $_.Name)
                $p = Start-Process -FilePath $setup -ArgumentList @(
                    '--uninstall',
                    '--system-level',
                    '--verbose-logging',
                    '--force-uninstall'
                ) -Wait -PassThru -WindowStyle Hidden
                # Track the highest non-zero exit code so we surface a real
                # failure even if a later per-version call happened to be 0.
                if ($p.ExitCode -gt $worst) { $worst = $p.ExitCode }
                Write-Output ("setup.exe ({0}) exit={1}" -f $_.Name, $p.ExitCode)
            }
        }
}
if (-not $ran) { Write-Output 'edge: setup.exe was not found; nothing to uninstall.' }
exit $worst
"""
    log.info("Edge uninstall [4/5]: invoking setup.exe --force-uninstall (may take minutes)…")
    res = ps.run(setup_script, timeout=timeout, label="edge:setup")
    log.info(
        "Edge uninstall [4/5]: setup.exe returned rc=%s (ok=%s). stdout=%r",
        res.returncode,
        res.ok,
        res.stdout.strip(),
    )

    # ------------------------------------------------------------------
    # Phase 5 (C): force-delete fallback when setup.exe was blocked.
    # ------------------------------------------------------------------
    if res.returncode == EDGE_EXIT_BLOCKED and force_delete_on_block:
        log.info(
            "Edge uninstall [5/5]: setup.exe returned %s (blocked); "
            "falling through to force-delete fallback…",
            EDGE_EXIT_BLOCKED,
        )
        fd_res = force_delete_edge()
        log.info(
            "Edge uninstall [5/5]: force-delete rc=%s (ok=%s). stdout=%r",
            fd_res.returncode,
            fd_res.ok,
            fd_res.stdout.strip(),
        )
        # Combine outputs. If the force-delete produced anything, promote
        # the overall result to success — Edge is gone (or as gone as it
        # can be without a full OS reinstall).
        combined_stdout = "\n".join(x for x in (res.stdout.strip(), fd_res.stdout.strip()) if x)
        if fd_res.ok:
            res.ok = True
            res.returncode = 0
            res.error = (
                f"setup.exe returned {EDGE_EXIT_BLOCKED} (uninstall was blocked by the OS); "
                "Edge was removed via the force-delete fallback instead. "
                "Windows Update reinstall was blocked via DoNotUpdateToEdgeWithChromium=1."
            )
        else:
            res.error = (
                f"Edge setup.exe was blocked (exit {EDGE_EXIT_BLOCKED}) and the "
                f"force-delete fallback also failed: {fd_res.error or 'see log for details'}."
            )
        res.stdout = combined_stdout

    elif res.returncode == EDGE_EXIT_BLOCKED:
        # Force-delete disabled by caller — surface the actionable message.
        res.ok = False
        res.error = (
            f"Edge setup.exe refused to uninstall (exit {EDGE_EXIT_BLOCKED}: "
            "'Uninstall was blocked for this product'). Enable "
            "force_delete_on_block=True (default) to force removal, or "
            "uninstall from Settings > Apps > Installed apps > Microsoft Edge "
            "after the EEA JSON patch (already applied) takes effect."
        )

    # ------------------------------------------------------------------
    # Deprovision: prevents Windows from re-installing Edge for new users.
    # ------------------------------------------------------------------
    if deprovision:
        log.info("Edge uninstall: deprovisioning AppX entry…")
        deprov = ps.run(
            "Get-AppxProvisionedPackage -Online | "
            "Where-Object { $_.DisplayName -like 'Microsoft.MicrosoftEdge*' } | "
            "Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue",
            timeout=120,
            label="edge:deprovision",
        )
        if deprov.stdout.strip():
            res.stdout = (res.stdout + "\n" + deprov.stdout).strip()

    if res.ok and not res.stdout.strip():
        res.stdout = "Edge uninstall completed."
    log.info("Edge uninstall: done (ok=%s)", res.ok)
    return res


def remove_package(
    pkg: AppxPackage,
    *,
    all_users: bool = True,
    deprovision: bool = True,
) -> ps.PSResult:
    """Remove an AppX package, optionally deprovisioning it.

    Runs each removal step as a discrete PowerShell call so a thrown error
    in one step does not silently swallow the others. The overall result is
    considered ``ok`` when *any* removal pathway succeeded (per-user remove,
    full-name remove, or deprovision for a previously installed package).

    For ``NonRemovable`` packages the registry lock is cleared first
    (requires Administrator). Chromium Edge is special-cased to its own
    ``setup.exe`` uninstaller because ``Remove-AppxPackage`` cannot remove it.
    """
    if dryrun.is_enabled():
        return dryrun.dry_result(f"would remove AppX package '{pkg.name}'")

    # Chromium Edge ignores Remove-AppxPackage; route it to the real uninstaller
    # so removal actually happens (and the batch doesn't stack failed timeouts).
    if is_edge_chromium(pkg):
        return remove_edge_chromium(deprovision=deprovision)

    log = _log()
    log.info(
        "Removing AppX '%s' (full_name=%s, non_removable=%s, all_users=%s, deprovision=%s)",
        pkg.name,
        pkg.full_name or "-",
        pkg.is_non_removable,
        all_users,
        deprovision,
    )

    # Real removal invalidates any cached listing.
    invalidate_cache()
    scope = "-AllUsers" if all_users else ""
    name_q = ps.ps_quote(pkg.name)
    steps: list[tuple[str, ps.PSResult]] = []

    # ---- NonRemovable registry-unlock ----
    # Windows stores a "NonRemovable" DWORD under the package's AppxAllUserStore
    # registry key. Deleting it (as Administrator) allows normal removal.
    if pkg.is_non_removable:
        unlock = (
            f"$_fn = (Get-AppxPackage -Name {name_q} | "
            "Select-Object -ExpandProperty PackageFullName -ErrorAction SilentlyContinue);"
            "if ($_fn) {"
            '  $_key = "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Appx\\AppxAllUserStore\\Applications\\$_fn";'
            "  if (Test-Path $_key) {"
            "    Remove-ItemProperty -Path $_key -Name 'NonRemovable' -ErrorAction SilentlyContinue"
            "  }"
            "}"
        )
        log.info("  Step 1/N: unlock NonRemovable registry for '%s'", pkg.name)
        steps.append(("unlock", ps.run(unlock, timeout=60, label=f"unlock {pkg.name}")))

    # ---- Remove for installed users ----
    log.info(
        "  Step: Remove-AppxPackage -Name '%s' (scope=%s, timeout=180s)",
        pkg.name,
        scope or "current-user",
    )
    steps.append(
        (
            "remove-by-name",
            ps.run(
                f"Get-AppxPackage {scope} -Name {name_q} | "
                "Remove-AppxPackage -ErrorAction SilentlyContinue",
                timeout=180,
                label=f"remove-by-name {pkg.name}",
            ),
        )
    )

    if pkg.full_name:
        log.info("  Step: Remove-AppxPackage -Package '%s' (timeout=180s)", pkg.full_name)
        steps.append(
            (
                "remove-by-fullname",
                ps.run(
                    f"Remove-AppxPackage {scope} -Package {ps.ps_quote(pkg.full_name)} "
                    "-ErrorAction SilentlyContinue",
                    timeout=180,
                    label=f"remove-by-fullname {pkg.name}",
                ),
            )
        )

    # ---- Deprovision (prevents re-install for new users / Windows Update) ----
    if deprovision:
        log.info("  Step: Remove-AppxProvisionedPackage '%s' (timeout=180s)", pkg.name)
        steps.append(
            (
                "deprovision",
                ps.run(
                    "Get-AppxProvisionedPackage -Online | "
                    f"Where-Object {{ $_.DisplayName -eq {name_q} }} | "
                    "Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue",
                    timeout=180,
                    label=f"deprovision {pkg.name}",
                ),
            )
        )

    # Aggregate: normally we consider "any removal step succeeded" as OK.
    # However, "deprovision only" is misleading — it stops NEW user profiles
    # from getting the package on the next feature update, but the currently
    # installed copy stays on disk. So track the two categories separately.
    real_remove_steps = [r for lbl, r in steps if lbl in ("remove-by-name", "remove-by-fullname")]
    deprov_steps = [r for lbl, r in steps if lbl == "deprovision"]
    real_remove_ok = any(r.ok for r in real_remove_steps)
    deprov_ok = any(r.ok for r in deprov_steps)

    log.info(
        "AppX '%s' step summary: %s -> real_remove=%s, deprovision=%s",
        pkg.name,
        ", ".join(f"{lbl}={'OK' if r.ok else f'FAIL(rc={r.returncode})'}" for lbl, r in steps),
        "OK" if real_remove_ok else "FAIL",
        "OK" if deprov_ok else "FAIL",
    )
    combined_stdout = "\n".join(r.stdout.strip() for _, r in steps if r.stdout and r.stdout.strip())
    combined_stderr = "\n".join(r.stderr.strip() for _, r in steps if r.stderr and r.stderr.strip())

    # If the actual removal was blocked by the OS but deprovision worked,
    # try the force-delete fallback so the user actually gets what they
    # asked for (the package gone from disk), not just "won't be given to
    # new users someday". Skip when we don't even know the install location.
    force_res: ps.PSResult | None = None
    if not real_remove_ok and _is_os_protected(combined_stderr):
        log.info(
            "AppX '%s': OS-protected removal (HRESULT %s); "
            "attempting force-delete fallback (WindowsApps takeown + delete)…",
            pkg.name,
            _ERROR_NOT_SUPPORTED,
        )
        force_res = force_delete_appx(pkg)
        log.info(
            "AppX '%s' force-delete: rc=%s (ok=%s). stdout=%r",
            pkg.name,
            force_res.returncode,
            force_res.ok,
            force_res.stdout.strip(),
        )
        if force_res.stdout.strip():
            combined_stdout = (combined_stdout + "\n" + force_res.stdout.strip()).strip()
        if force_res.stderr.strip():
            combined_stderr = (combined_stderr + "\n" + force_res.stderr.strip()).strip()

    # Final verdict.
    force_ok = bool(force_res and force_res.ok)
    overall_ok = real_remove_ok or force_ok or deprov_ok

    if real_remove_ok or force_ok:
        combined_error = ""
    elif deprov_ok:
        # We only deprovisioned. Be honest — the installed copy is still there.
        combined_error = (
            f"Partial: '{pkg.name}' was deprovisioned (new user profiles won't "
            "get it) but the currently installed copy could not be removed on "
            "this build. Try re-running with the tool up-to-date, or use the "
            "force-delete option."
        )
    elif _is_os_protected(combined_stderr):
        combined_error = (
            f"Windows refuses to remove '{pkg.name}' on this build "
            f"(HRESULT {_ERROR_NOT_SUPPORTED}). Both Remove-AppxPackage and "
            "the force-delete fallback failed. It is treated as an OS component "
            "and cannot be uninstalled by any supported means. Disable the "
            "associated Windows feature instead (Settings > Notifications / "
            "Personalisation, or Group Policy)."
        )
    else:
        combined_error = combined_stderr or "All removal steps failed."

    return ps.PSResult(
        ok=overall_ok,
        returncode=0 if overall_ok else 1,
        stdout=combined_stdout,
        stderr=combined_stderr,
        error=combined_error,
    )


def _is_os_protected(text: str) -> bool:
    """Return True when ``text`` contains an HRESULT that indicates Windows
    itself refused to remove the package (as opposed to a transient error).
    """
    if not text:
        return False
    low = text.lower()
    return any(marker in low for marker in _OS_PROTECTED_MARKERS)


def restore_package(pkg: AppxPackage) -> ps.PSResult:
    """Attempt to re-register / reinstall a previously removed package."""
    invalidate_cache()
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$pkg={ps.ps_quote(pkg.name)};"
        "Get-AppxPackage -AllUsers -Name $pkg | ForEach-Object {"
        "  Add-AppxPackage -DisableDevelopmentMode -Register "
        '    "$($_.InstallLocation)\\AppXManifest.xml"'
        "}"
    )
    res = ps.run(script, timeout=180)
    if res.ok and not res.stdout.strip():
        res.stdout = (
            f"No on-disk manifest found for '{pkg.name}'. "
            "Reinstall it from the Microsoft Store if needed."
        )
    return res
