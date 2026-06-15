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
from app.core.paths import resource_path

# Matches the version/arch/publisher-hash suffix on AppX PackageName values,
# e.g. ``Microsoft.BingNews_4.55.1.0_x64__8wekyb3d8bbwe``.
_PKG_FULLNAME_SUFFIX = re.compile(r"_\d+(?:\.\d+)+_[a-zA-Z0-9]+(?:_[a-zA-Z0-9]*)?_[a-z0-9]+$")

# Schema version expected in ``app/core/data/bloatware.json``.
CATALOG_VERSION = 3

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
    (requires Administrator).
    """
    if dryrun.is_enabled():
        return dryrun.dry_result(f"would remove AppX package '{pkg.name}'")

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
        steps.append(("unlock", ps.run(unlock, timeout=60)))

    # ---- Remove for installed users ----
    steps.append(
        (
            "remove-by-name",
            ps.run(
                f"Get-AppxPackage {scope} -Name {name_q} | "
                "Remove-AppxPackage -ErrorAction SilentlyContinue",
                timeout=180,
            ),
        )
    )

    if pkg.full_name:
        steps.append(
            (
                "remove-by-fullname",
                ps.run(
                    f"Remove-AppxPackage {scope} -Package {ps.ps_quote(pkg.full_name)} "
                    "-ErrorAction SilentlyContinue",
                    timeout=180,
                ),
            )
        )

    # ---- Deprovision (prevents re-install for new users / Windows Update) ----
    if deprovision:
        steps.append(
            (
                "deprovision",
                ps.run(
                    "Get-AppxProvisionedPackage -Online | "
                    f"Where-Object {{ $_.DisplayName -eq {name_q} }} | "
                    "Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue",
                    timeout=180,
                ),
            )
        )

    # Aggregate: succeed if any removal/deprovision step ran without error.
    removal_steps = [r for label, r in steps if label != "unlock"]
    overall_ok = any(r.ok for r in removal_steps)
    combined_stdout = "\n".join(r.stdout.strip() for _, r in steps if r.stdout and r.stdout.strip())
    combined_stderr = "\n".join(r.stderr.strip() for _, r in steps if r.stderr and r.stderr.strip())
    combined_error = "" if overall_ok else (combined_stderr or "All removal steps failed.")

    return ps.PSResult(
        ok=overall_ok,
        returncode=0 if overall_ok else 1,
        stdout=combined_stdout,
        stderr=combined_stderr,
        error=combined_error,
    )


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
