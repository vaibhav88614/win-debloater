"""List and control Windows services."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from app.core import dryrun
from app.core import powershell as ps

# Services that should never be touched even in Advanced mode.
PROTECTED_SERVICES = {
    "rpcss",
    "dcomlaunch",
    "rpceptmapper",
    "plugplay",
    "power",
    "winmgmt",
    "lsm",
    "samss",
    "eventlog",
    "schedule",
    "profsvc",
    "dnscache",
    "nsi",
    "brokerinfrastructure",
    "systemeventsbroker",
    "coremessagingregistrar",
    "wuauserv",
    "trustedinstaller",
    "cryptsvc",
    "bfe",
    "mpssvc",
    "windefend",
    "wscsvc",
    "gpsvc",
    "themes",
    "audiosrv",
    "audioendpointbuilder",
    "netprofm",
    "nlasvc",
    "wlansvc",
    "lanmanworkstation",
    "lanmanserver",
}

# Common services frequently disabled for privacy/performance (Safe mode list).
SAFE_TWEAKABLE = {
    "diagtrack": "Connected User Experiences and Telemetry",
    "dmwappushservice": "Device Management WAP Push (telemetry)",
    "retaildemo": "Retail Demo Service",
    "mapsbroker": "Downloaded Maps Manager",
    "wisvc": "Windows Insider Service",
    "remoteregistry": "Remote Registry",
    "fax": "Fax",
    "xblauthmanager": "Xbox Live Auth Manager",
    "xblgamesave": "Xbox Live Game Save",
    "xboxgipsvc": "Xbox Accessory Management",
    "xboxnetapisvc": "Xbox Live Networking",
    "wsearch": "Windows Search (indexing)",
    "sysmain": "SysMain (Superfetch)",
    "phonesvc": "Phone Service",
    "printnotify": "Printer Extensions and Notifications",
}

START_TYPES = ["Automatic", "AutomaticDelayedStart", "Manual", "Disabled"]


@dataclass
class ServiceInfo:
    name: str
    display_name: str = ""
    status: str = ""  # Running / Stopped
    start_type: str = ""  # Auto / Manual / Disabled
    description: str = ""
    can_stop: bool = True
    is_protected: bool = False
    is_safe_tweak: bool = False

    @property
    def safe(self) -> bool:
        """Safe-mode visibility: only well-known tweakable services."""
        return self.is_safe_tweak and not self.is_protected


def list_services() -> list[ServiceInfo]:
    """List all Windows services with status and startup type."""
    if sys.platform != "win32":
        return []
    script = (
        "Get-CimInstance Win32_Service | "
        "Select-Object Name, DisplayName, State, StartMode, Description, "
        "@{N='CanStop';E={$_.AcceptStop}}"
    )
    res = ps.run_json(script, timeout=120)
    services: list[ServiceInfo] = []
    if not res.ok:
        return services

    for item in res.items:
        name = (item.get("Name") or "").strip()
        if not name:
            continue
        low = name.lower()
        svc = ServiceInfo(
            name=name,
            display_name=item.get("DisplayName") or name,
            status=item.get("State") or "",
            start_type=item.get("StartMode") or "",
            description=(item.get("Description") or "").strip(),
            can_stop=bool(item.get("CanStop")),
            is_protected=low in PROTECTED_SERVICES,
            is_safe_tweak=low in SAFE_TWEAKABLE,
        )
        services.append(svc)

    services.sort(key=lambda s: s.display_name.lower())
    return services


def set_start_type(name: str, start_type: str) -> ps.PSResult:
    """Set a service's startup type (Automatic/Manual/Disabled)."""
    if not name or not name.strip():
        return ps.PSResult(ok=False, returncode=-1, error="Service name is required.")
    if name.lower() in PROTECTED_SERVICES:
        return ps.PSResult(
            ok=False, returncode=-1, error=f"'{name}' is protected and cannot be changed."
        )
    if start_type not in START_TYPES:
        return ps.PSResult(ok=False, returncode=-1, error=f"Invalid start type '{start_type}'.")
    if dryrun.is_enabled():
        return dryrun.dry_result(f"would set '{name}' startup to {start_type}")
    script = f"Set-Service -Name {ps.ps_quote(name)} -StartupType {start_type}"
    return ps.run(script, timeout=60)


def stop_service(name: str) -> ps.PSResult:
    if not name or not name.strip():
        return ps.PSResult(ok=False, returncode=-1, error="Service name is required.")
    if name.lower() in PROTECTED_SERVICES:
        return ps.PSResult(
            ok=False, returncode=-1, error=f"'{name}' is protected and cannot be stopped."
        )
    if dryrun.is_enabled():
        return dryrun.dry_result(f"would stop service '{name}'")
    return ps.run(f"Stop-Service -Name {ps.ps_quote(name)} -Force -ErrorAction Stop", timeout=60)


def start_service(name: str) -> ps.PSResult:
    if not name or not name.strip():
        return ps.PSResult(ok=False, returncode=-1, error="Service name is required.")
    if dryrun.is_enabled():
        return dryrun.dry_result(f"would start service '{name}'")
    return ps.run(f"Start-Service -Name {ps.ps_quote(name)} -ErrorAction Stop", timeout=60)
