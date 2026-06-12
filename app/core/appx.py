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
    # Catalog enrichment
    friendly_name: str = ""
    category: str = "Other"
    safe: bool = True
    description: str = ""
    catalog_id: str = ""

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
    """Match an installed package name against catalog patterns."""
    for entry in catalog:
        pattern = entry.get("id", "")
        if not pattern:
            continue
        if pattern == package_name:
            return entry
    # Fallback to wildcard / case-insensitive contains matching.
    low = package_name.lower()
    for entry in catalog:
        pattern = entry.get("id", "")
        if "*" in pattern and fnmatch(low, pattern.lower()):
            return entry
    return None


def list_installed(include_all_users: bool = True) -> list[AppxPackage]:
    """List installed AppX packages, enriched with catalog metadata.

    Returns packages for the current user (plus provisioned packages).
    """
    catalog = load_catalog()
    packages: dict[str, AppxPackage] = {}

    scope = "-AllUsers" if include_all_users else ""
    user_script = (
        f"Get-AppxPackage {scope} | "
        "Where-Object { $_.NonRemovable -ne $true } | "
        "Select-Object Name, PackageFullName, Publisher, Version, InstallLocation"
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
            )

    # Provisioned packages (apply to new users / fresh installs).
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
        else:
            pkg.friendly_name = ""
            pkg.category = "Other (not in catalog)"
            pkg.safe = False  # Unknown packages are treated as advanced-only.
        result.append(pkg)

    result.sort(key=lambda p: (p.category, p.display_name.lower()))
    return result


def remove_package(pkg: AppxPackage, *, all_users: bool = True, deprovision: bool = True) -> ps.PSResult:
    """Remove an installed AppX package (and optionally deprovision it).

    Returns the PSResult of the removal command.
    """
    commands: list[str] = []

    target = pkg.full_name or pkg.name
    scope = "-AllUsers" if all_users else ""
    # Remove for installed users.
    commands.append(
        f"Get-AppxPackage {scope} -Name '{pkg.name}' | Remove-AppxPackage -ErrorAction SilentlyContinue"
    )
    if pkg.full_name:
        commands.append(
            f"Remove-AppxPackage {scope} -Package '{pkg.full_name}' -ErrorAction SilentlyContinue"
        )

    # Deprovision so it won't return for new users / feature updates.
    if deprovision:
        commands.append(
            "Get-AppxProvisionedPackage -Online | "
            f"Where-Object {{ $_.DisplayName -eq '{pkg.name}' }} | "
            "Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue"
        )

    script = "; ".join(commands)
    return ps.run(script, timeout=240)


def restore_package(pkg: AppxPackage) -> ps.PSResult:
    """Attempt to re-register / reinstall a previously removed package."""
    # Re-register from any remaining on-disk manifest for all users.
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$pkg='{pkg.name}';"
        "Get-AppxPackage -AllUsers -Name $pkg | ForEach-Object {"
        "  Add-AppxPackage -DisableDevelopmentMode -Register "
        "    \"$($_.InstallLocation)\\AppXManifest.xml\""
        "}"
    )
    res = ps.run(script, timeout=180)
    # If nothing was re-registered, hint the user toward the Store.
    if res.ok and not res.stdout.strip():
        res.stdout = (
            f"No on-disk manifest found for '{pkg.name}'. "
            "Reinstall it from the Microsoft Store if needed."
        )
    return res
