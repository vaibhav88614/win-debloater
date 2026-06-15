"""List, remove, and restore Windows Store (AppX) packages."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Optional

from app.core import powershell as ps
from app.core.paths import resource_path


@dataclass
class AppxPackage:
    """A single installed AppX package mapped to its catalog metadata."""

    name: str                      # PackageName, e.g. Microsoft.BingNews
    full_name: str                 # PackageFullName (versioned)
    publisher: str = ""
    version: str = ""
    install_location: str = ""
    is_provisioned: bool = False   # Present in the provisioned (per-image) store
    is_non_removable: bool = False # Windows marks this NonRemovable in the AppX DB
    # Catalog enrichment
    friendly_name: str = ""
    category: str = "Other"
    safe: bool = True
    description: str = ""
    catalog_id: str = ""
    removal_note: str = ""         # Extra guidance shown in the UI tooltip

    @property
    def display_name(self) -> str:
        return self.friendly_name or self.name


def load_catalog() -> list[dict]:
    """Load the curated bloatware catalog from bundled JSON."""
    path = resource_path("app", "core", "data", "bloatware.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("packages", [])
    except (OSError, json.JSONDecodeError):
        return []


def _match_catalog(package_name: str, catalog: list[dict]) -> Optional[dict]:
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


def list_installed(include_all_users: bool = True) -> list[AppxPackage]:
    """List *all* AppX packages (including NonRemovable), enriched with catalog data.

    ``is_non_removable`` is set on packages Windows has flagged as protected;
    the caller (UI) decides whether to display/allow actions on them.
    """
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
    prov_script = (
        "Get-AppxProvisionedPackage -Online | "
        "Select-Object DisplayName, PackageName"
    )
    prov = ps.run_json(prov_script, timeout=180)
    if prov.ok:
        for item in prov.items:
            disp = item.get("DisplayName") or ""
            pkgname = item.get("PackageName") or ""
            if not disp:
                continue
            if disp in packages:
                packages[disp].is_provisioned = True
            else:
                packages[disp] = AppxPackage(
                    name=disp,
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
    return result


def remove_package(
    pkg: AppxPackage,
    *,
    all_users: bool = True,
    deprovision: bool = True,
) -> ps.PSResult:
    """Remove an AppX package, optionally deprovisioning it.

    For ``NonRemovable`` packages the registry lock is cleared first via a
    well-known trick used by debloater projects (requires Administrator).
    """
    scope = "-AllUsers" if all_users else ""
    cmds: list[str] = []

    # ---- NonRemovable registry-unlock ----
    # Windows stores a "NonRemovable" DWORD under the package's AppxAllUserStore
    # registry key. Deleting it (as Administrator) allows normal removal.
    if pkg.is_non_removable:
        cmds.append(
            "$_fn = (Get-AppxPackage -Name '{name}' | Select-Object -ExpandProperty PackageFullName -ErrorAction SilentlyContinue);"
            "$_key = \"HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Appx\\AppxAllUserStore\\Applications\\$_fn\";"
            "if (Test-Path $_key) {{ Remove-ItemProperty -Path $_key -Name 'NonRemovable' -ErrorAction SilentlyContinue }}"
            .format(name=pkg.name)
        )

    # ---- Remove for installed users ----
    cmds.append(
        f"Get-AppxPackage {scope} -Name '{pkg.name}' | Remove-AppxPackage -ErrorAction SilentlyContinue"
    )
    if pkg.full_name:
        cmds.append(
            f"Remove-AppxPackage {scope} -Package '{pkg.full_name}' -ErrorAction SilentlyContinue"
        )

    # ---- Deprovision (prevents re-install for new users / Windows Update) ----
    if deprovision:
        cmds.append(
            "Get-AppxProvisionedPackage -Online | "
            f"Where-Object {{ $_.DisplayName -eq '{pkg.name}' }} | "
            "Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue"
        )

    return ps.run("; ".join(cmds), timeout=300)


def restore_package(pkg: AppxPackage) -> ps.PSResult:
    """Attempt to re-register / reinstall a previously removed package."""
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$pkg='{pkg.name}';"
        "Get-AppxPackage -AllUsers -Name $pkg | ForEach-Object {"
        "  Add-AppxPackage -DisableDevelopmentMode -Register "
        "    \"$($_.InstallLocation)\\AppXManifest.xml\""
        "}"
    )
    res = ps.run(script, timeout=180)
    if res.ok and not res.stdout.strip():
        res.stdout = (
            f"No on-disk manifest found for '{pkg.name}'. "
            "Reinstall it from the Microsoft Store if needed."
        )
    return res
