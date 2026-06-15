# Windows Debloater & Task Control

A modern PySide6 desktop tool (packaged as a single `.exe`) to remove Windows
bloatware, control background services and scheduled tasks, and detect/act on
suspicious processes - with safety guardrails, restore points, and undo.

![tabs](docs-placeholder) <!-- screenshots optional -->

## Features

- **Bloatware removal** - uninstall Windows Store (AppX) apps by checkbox.
  Removals are logged so you can attempt to restore them later.
- **Services control** - stop/start services and change startup type
  (Automatic / Manual / Disabled). Critical system services are protected.
- **Scheduled tasks** - enable/disable tasks (telemetry/diagnostic tasks are
  surfaced in Safe mode). Fully reversible.
- **Processes & suspicious detection** - live process list scored by heuristics
  (unusual paths, unsigned binaries, system-binary impersonation, random names,
  autostart + network activity). Suspend, resume, end, or locate.
- **Safe vs Advanced modes** - Safe mode shows only known, low-risk, reversible
  items. Advanced mode unlocks everything (with extra confirmations).
- **Safety net** - optional **System Restore point** before destructive batches,
  confirmation dialogs, and a full **action history with one-click undo**.

## Safety model

- **Safe mode (default):** only curated, well-known, reinstallable apps;
  common privacy/performance services; and known telemetry tasks are shown.
- **Advanced mode:** shows all packages/services/tasks. Hard-coded protected
  lists still prevent touching OS-critical services and processes.
- Every destructive action is written to a JSON history at
  `%LOCALAPPDATA%\WinDebloater\action_history.json`.

## Requirements

- Windows 10/11
- Administrator rights for changes (the app self-elevates via UAC)
- For running from source: Python 3.10+ (developed on 3.13)

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

Use `run.py --no-elevate` during development to skip the UAC prompt
(listing works; system changes may fail without elevation).

## Build the .exe

```powershell
.\.venv\Scripts\python.exe -m PyInstaller app.spec --noconfirm --clean
```

The single-file executable is produced at `dist\WinDebloater.exe`. It carries an
embedded `requireAdministrator` manifest, so double-clicking it triggers a UAC
prompt automatically.

## Project layout

```
app/
  main.py            entry + UAC self-elevation
  core/
    elevation.py     admin detection / relaunch
    powershell.py    safe PowerShell exec + JSON parsing
    appx.py          list/remove/restore Store apps
    services.py      list/control services
    scheduled_tasks.py  list/enable/disable tasks
    processes.py     psutil process control
    suspicious.py    suspicion scoring heuristics
    restore.py       system restore points
    actionlog.py     history + undo dispatcher
    data/bloatware.json   curated catalog
  ui/
    main_window.py   tabs + global controls
    *_tab.py         per-feature tabs
    workers.py       QThread workers
    widgets.py       shared widgets
  resources/style.qss  dark theme
```

## Notes & limitations

- Some removals/changes require a real elevated session to take full effect;
  validate on a test machine/VM first.
- Removing provisioned packages affects new user profiles too.
- "Restore" of an AppX app re-registers it from any on-disk manifest; if none
  remains, reinstall from the Microsoft Store.
- Suspicion scores are heuristic signals, not a verdict - review before acting.

## License

For personal use. Review actions carefully; use at your own risk.
