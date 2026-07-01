"""List and uninstall traditional Win32 / MSI programs (Add-or-Remove Programs).

The Bloatware tab handles Windows Store (AppX) packages. This module covers
everything in the classic *Programs and Features* list — MSI products such as
the Windows Software Development Kit, vendor tools, runtimes, and desktop apps —
by reading the standard Uninstall registry hives and invoking each entry's
uninstall command silently where possible.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from app.core import dryrun
from app.core import powershell as ps

# Matches a Windows Installer ProductCode GUID, e.g. {D2DE764E-...}.
_GUID_RE = re.compile(
    r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}"
)

# ReleaseType values that indicate an OS update rather than a real program.
_UPDATE_RELEASE_TYPES = {"security update", "update", "hotfix", "servicepack"}


@dataclass
class ProgramGroup:
    """A named bundle of related programs selectable in one click.

    ``name_patterns`` are case-insensitive regexes matched against the program's
    display name; ``publisher_contains`` (optional) further constrains matches to
    a publisher substring so, e.g., only *Microsoft's* SDK components qualify.
    """

    id: str
    name: str
    description: str
    name_patterns: list[str]
    publisher_contains: str = ""


# Built-in groups for the Installed Programs tab. These target large multi-part
# suites that are tedious to tick one by one (the Windows SDK alone installs
# ~40 separate MSI components).
BUILTIN_GROUPS: list[ProgramGroup] = [
    ProgramGroup(
        id="windows_sdk",
        name="Windows SDK (all components)",
        description=(
            "Every Windows Software Development Kit component — headers, libraries, "
            "tools, redistributables, and the Desktop/Mobile/IoT/Team extension SDKs."
        ),
        name_patterns=[r"\bSDK\b"],
        publisher_contains="microsoft",
    ),
    ProgramGroup(
        id="vc_redist",
        name="Visual C++ Redistributables",
        description="Microsoft Visual C++ runtime redistributable packages (all versions/arches).",
        name_patterns=[r"visual c\+\+.*redistributable"],
        publisher_contains="microsoft",
    ),
    ProgramGroup(
        id="dotnet_runtimes",
        name=".NET runtimes & SDKs",
        description=(
            "Microsoft .NET / .NET Core runtimes, hosting bundles, and SDKs. "
            "Only remove these if no app or tool still needs them."
        ),
        name_patterns=[r"microsoft \.net", r"\.net (core |)(runtime|sdk|host)"],
        publisher_contains="microsoft",
    ),
]


def load_groups() -> list[ProgramGroup]:
    """Return the available program groups (currently the built-in set)."""
    return list(BUILTIN_GROUPS)


def match_group(group: ProgramGroup, items: list[Program]) -> list[Program]:
    """Return the subset of ``items`` matched by ``group``.

    A program matches when its name matches any pattern AND (if given) its
    publisher contains ``publisher_contains`` (both case-insensitive).
    """
    patterns = [re.compile(p, re.IGNORECASE) for p in group.name_patterns]
    pub = group.publisher_contains.lower()
    out: list[Program] = []
    for prog in items:
        if pub and pub not in prog.publisher.lower():
            continue
        if any(rx.search(prog.name) for rx in patterns):
            out.append(prog)
    return out


@dataclass
class Program:
    """A single installed Win32/MSI program from the Uninstall registry."""

    name: str
    version: str = ""
    publisher: str = ""
    install_location: str = ""
    uninstall_string: str = ""
    quiet_uninstall_string: str = ""
    product_code: str = ""  # MSI ProductCode GUID when applicable
    hive: str = "HKLM"  # HKLM | HKLM32 | HKCU
    is_msi: bool = False
    is_system_component: bool = False
    is_update: bool = False
    estimated_kb: int = 0

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def size_mb(self) -> float:
        return round(self.estimated_kb / 1024.0, 1) if self.estimated_kb else 0.0

    def dedup_key(self) -> tuple[str, str, str]:
        return (self.name.lower(), self.version, self.product_code.lower())


def _guid_from(value: str) -> str:
    m = _GUID_RE.search(value or "")
    return m.group(0) if m else ""


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _program_from_item(item: dict) -> Program | None:
    name = (item.get("DisplayName") or "").strip()
    if not name:
        return None

    uninstall = (item.get("UninstallString") or "").strip()
    quiet = (item.get("QuietUninstallString") or "").strip()
    child = (item.get("PSChildName") or "").strip()

    product_code = child if _GUID_RE.fullmatch(child) else _guid_from(uninstall)
    win_installer = bool(_to_int(item.get("WindowsInstaller")))
    is_msi = win_installer or product_code != "" or "msiexec" in uninstall.lower()

    release_type = (item.get("ReleaseType") or "").strip().lower()
    parent = (item.get("ParentKeyName") or "").strip()
    is_update = release_type in _UPDATE_RELEASE_TYPES or bool(parent)

    return Program(
        name=name,
        version=(item.get("DisplayVersion") or "").strip(),
        publisher=(item.get("Publisher") or "").strip(),
        install_location=(item.get("InstallLocation") or "").strip(),
        uninstall_string=uninstall,
        quiet_uninstall_string=quiet,
        product_code=product_code,
        hive=(item.get("Hive") or "HKLM").strip() or "HKLM",
        is_msi=is_msi,
        is_system_component=bool(_to_int(item.get("SystemComponent"))),
        is_update=is_update,
        estimated_kb=_to_int(item.get("EstimatedSize")),
    )


_LIST_SCRIPT = r"""
$paths = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
Get-ItemProperty -Path $paths -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName } |
    ForEach-Object {
        $hive = if ($_.PSPath -match 'WOW6432Node') { 'HKLM32' }
                elseif ($_.PSPath -match 'HKEY_CURRENT_USER') { 'HKCU' }
                else { 'HKLM' }
        [pscustomobject]@{
            DisplayName          = $_.DisplayName
            DisplayVersion       = $_.DisplayVersion
            Publisher            = $_.Publisher
            InstallLocation      = $_.InstallLocation
            UninstallString      = $_.UninstallString
            QuietUninstallString = $_.QuietUninstallString
            PSChildName          = $_.PSChildName
            SystemComponent      = $_.SystemComponent
            WindowsInstaller     = $_.WindowsInstaller
            ReleaseType          = $_.ReleaseType
            ParentKeyName        = $_.ParentKeyName
            EstimatedSize        = $_.EstimatedSize
            Hive                 = $hive
        }
    }
"""


def list_programs() -> list[Program]:
    """Return every installed Win32/MSI program from the Uninstall registry hives.

    Entries without an ``UninstallString`` (and not MSI) are dropped because
    they cannot be removed. Results are de-duplicated by (name, version, GUID)
    and sorted by display name.
    """
    if sys.platform != "win32":
        return []

    res = ps.run_json(_LIST_SCRIPT, timeout=120)
    if not res.ok:
        return []

    seen: set[tuple[str, str, str]] = set()
    out: list[Program] = []
    for item in res.items:
        prog = _program_from_item(item)
        if prog is None:
            continue
        # Must be actionable: either MSI (we can synthesize a command) or have
        # some uninstall string to run.
        if not prog.is_msi and not prog.uninstall_string:
            continue
        key = prog.dedup_key()
        if key in seen:
            continue
        seen.add(key)
        out.append(prog)

    out.sort(key=lambda p: p.display_name.lower())
    return out


def _msi_uninstall_command(product_code: str) -> str:
    """Build a fully silent msiexec uninstall command for a ProductCode."""
    code = ps.ps_quote(product_code)
    return (
        "$p = Start-Process -FilePath 'msiexec.exe' "
        f"-ArgumentList @('/x', {code}, '/qn', '/norestart') "
        "-Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode"
    )


def _string_uninstall_command(command_line: str, *, silent: bool) -> str:
    """Run a raw uninstall command line via cmd.exe and surface its exit code.

    Non-MSI uninstallers vary wildly; we run the provided string as-is (through
    ``cmd /c``) and wait for it. When ``silent`` is False the vendor UI may
    appear — that is expected for programs without a quiet uninstall string.
    """
    quoted = ps.ps_quote(command_line)
    return (
        "$p = Start-Process -FilePath 'cmd.exe' "
        f"-ArgumentList @('/c', {quoted}) "
        "-Wait -PassThru -WindowStyle Hidden; exit $p.ExitCode"
    )


# msiexec / installer exit codes that indicate success (or a benign no-op).
_OK_EXIT_CODES = {0, 1605, 3010, 1614, 1641}


def uninstall_program(program: Program, *, timeout: int = 900) -> ps.PSResult:
    """Uninstall a single Win32/MSI program.

    Strategy, in order of preference:
      1. Chromium Edge → route to the dedicated Edge uninstaller.
      2. MSI (has a ProductCode) → ``msiexec /x {GUID} /qn /norestart`` (silent).
      3. QuietUninstallString → run it (silent by design).
      4. UninstallString → run it (may show the vendor's uninstall UI).

    Returns a :class:`~app.core.powershell.PSResult`; ``ok`` is True when the
    launched process exits with a success/no-op code.
    """
    if dryrun.is_enabled():
        return dryrun.dry_result(f"would uninstall program '{program.name}'")

    # Edge shows up in the registry as an MSI stub, but that MSI does not fully
    # remove the browser. Delegate to the dedicated Edge uninstaller.
    if program.name.strip().lower() == "microsoft edge":
        from app.core import appx

        return appx.remove_edge_chromium()

    if program.is_msi and program.product_code:
        script = _msi_uninstall_command(program.product_code)
        silent = True
    elif program.quiet_uninstall_string:
        script = _string_uninstall_command(program.quiet_uninstall_string, silent=True)
        silent = True
    elif program.uninstall_string:
        script = _string_uninstall_command(program.uninstall_string, silent=False)
        silent = False
    else:
        return ps.PSResult(ok=False, returncode=-1, error="No uninstall command available.")

    res = ps.run(script, timeout=timeout)

    # Treat known success/no-op installer exit codes as success even though the
    # raw return code is non-zero (e.g. 3010 = success, reboot required).
    if res.returncode in _OK_EXIT_CODES:
        res.ok = True
        if not res.error and res.returncode == 3010:
            res.stdout = (res.stdout + "\nA reboot is required to finish removal.").strip()
        res.error = ""
    elif not res.ok and not res.error:
        note = "" if silent else " (the uninstaller may require manual interaction)"
        res.error = f"Uninstaller exited with code {res.returncode}{note}."

    return res
