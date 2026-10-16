# Omachines

A native Omarchy bar panel for Windows VM controls, agent connections and backups.

**Early preview:** v0.1 has been tested with one existing Omarchy Windows setup.
The optional SSH, Cua, Aside and backup features require separately configured
local bridge scripts. Those machine-specific scripts are not shipped here.
This is not a general-purpose VM installer or a marketplace release.

## Use

Click **VM** on the right of the bar. Right-click focuses/opens the Windows viewer;
middle-click checks connections. Closing the viewer leaves Windows running through
the existing `windows-vm-launch` wrapper.

- **Overview:** desktop, storage, open Aside, shared folder, background start and shutdown.
- **Agents:** independent SSH, desktop, Cua and Aside connection results; console
  recovery; a private diagnostic report containing only allowlisted fields.
- **Backups:** existing baseline/development records and a new complete cold backup.
- **Settings:** Aside integration, notifications and connection-check interval.
  Omarchy's bar widget settings also expose the backup-provider workspace and label.

Left/right arrows switch sections; up/down select actions; Enter selects;
1–4 choose a section; R checks connections; Escape closes. In Settings, Enter
toggles the selected option or cycles the interval. Shutdown, console handoff and
backup require a confirmation; Cancel receives initial keyboard focus.

## Install, update and remove

Tested on Omarchy 4.0.4 with its Quickshell shell, Python 3, and an existing
Omarchy Windows VM. No pip/npm packages or separate web server are needed.

```bash
python3 scripts/check.py
python3 install.py
# Only if you already have a reviewed, compatible backup provider:
python3 install.py --setup-workspace /absolute/path/to/WindowsVM
# Subsequent local updates:
python3 install.py
# Remove only the plugin, retaining machine, backups, and state:
python3 install.py --uninstall
```

The installer copies an explicit file list to
`~/.config/omarchy/plugins/atlastimed.omachines/`, preserves other widgets, and
backs up the previous plugin and shell configuration under
`~/.local/state/omachines/install-backups/`. Omarchy hot-reloads it without a
shell or Windows restart. A content-addressed `runtime/` directory prevents
Quickshell from retaining old QML components after an update. One release is
installed at a time; old releases remain in the private installer backups.
Updates are manual: update your checkout, run the checks, then rerun the installer.
Updates and file rollback leave the current shell settings untouched. Initial
placement, removal and an explicitly supplied workspace use Omarchy's native
settings API. If that final API step fails, the installed files and current
settings are retained for inspection. Malformed or symlinked `shell.json` files
are rejected before changing the installation.

Optional existing executable wrappers in `~/.local/bin/`:
`windows-vm-launch`, `windows-vm-ssh`, `windows-vm-mcp`, and
`windows-vm-aside-mcp`. Missing connections are shown as unavailable. The current
Windows adapter uses Omarchy's standard loopback ports 2222/8006 and `~/Windows`
sharing location. Supporting other providers/configurations needs another adapter.
Without the optional bridges, their controls are unavailable; the plugin does
not install or invent them. See [integration requirements](INTEGRATION.md).

## Backups and safety

The current backup provider is the reviewed `create-development-backup.sh` in
the Windows setup workspace. It must implement the Omachines stage markers and
the same root-owned lifecycle lock as Omarchy. The currently tested provider is
machine-specific and not included; a portable provider is future work. This repository stores no
credentials or VM images. The setup workspace is a trusted local administrative
script location, never a path supplied by a guest.

Backup opens a normal terminal and asks for the **Omarchy password**. It checks
update state and destination space, shuts Windows down, copies its disk/TPM/firmware
and private configuration, lists the archive and verifies SHA-256, then starts
Windows and checks its connections. Shared-folder files are separate. Older
backups are retained. Verification does not constitute a restore rehearsal.
The full backup contains signed-in browser state and private configuration and
must stay on the encrypted backup disk with its existing restricted permissions.

One worker lock prevents overlapping operations. Lifecycle workers survive bar
reloads. Each request has a one-use identity and a 120-second startup deadline;
expired or replaced requests cannot execute later. Installation/removal holds a
maintenance gate for the entire transaction and refuses pending or active work.
A stopped or interrupted worker is reported honestly; interrupted backups
leave `.incomplete` data for inspection. The provider holds Omarchy's lifecycle
lock while copying, preventing a viewer launch from starting Windows mid-copy.
Do not manually start/stop Docker containers during backup. If an operation
terminal is cancelled, inspect it and Windows before retrying.

## Performance and theme integration

One shell service is shared across all monitors. Its standard-library Python
backend checks local reachability every 15 seconds and probes the guest every
120 seconds by default (adjustable). Calls are bounded; there is no continuous
screenshot stream or passive reading of browser pages, bookmarks or account data.
Aside is probed only when already running; no AI agent is started by a check.
Connection readiness does **not** mean that an agent is working or idle.

The panel uses `qs.Ui`, `qs.Commons.Color` and `Style`, including fonts, scaled
spacing, borders, selection, corners and popup surfaces. It follows the active
Omarchy theme without a second theme configuration. It runs within the existing
shell. The developer test harness starts an isolated offscreen instance only.

## Development and validation

`scripts/check.py` runs Python tests, Omarchy manifest validation, qmllint and
Qt Quick interaction tests in the real Quickshell runtime. A regular
`qmltestrunner` cannot load Quickshell's executable-registered types. The harness
stages the installed Omarchy components and checks actual Qt mouse interactions,
confirmations, keyboard model behaviour and live light/dark token changes.

Playwright is appropriate for web previews and browser pages; it cannot drive
this native QML surface. Physical Hyprland panel screenshots and IPC checks are
separate from offscreen test evidence. Windows Cua controls the guest, not Linux.

Project files:

- `Service.qml`: shared backend lifecycle and JSON-line communication.
- `BarWidget.qml`, `MachineView.qml`, `StatusRow.qml`, `Model.js`: native UI.
- `backend/core.py`, `guest-status.ps1`: bounded Windows adapter and typed status.
- `backend/operations.py`: allowlisted actions, durable progress and backups.
- `backend/mcp.py`: short-lived MCP connections with timeouts and output limits.
- `backend/probes.py`: incremental bounded IO and owned probe-group cleanup.
- `backend/omachinesctl`: status, action, daemon and worker entry points.

Future scope: allocation editing with safe restart, tested restoration, portable
backup providers, multiple VMs, Linux adapters, and explicit agent task/session
coordination. CPU/RAM settings, restore and arbitrary agent execution are not
implemented in this version.

Contract reference: [Omarchy plugin development guide](https://plugins.omarchy.org/develop.html).

See [validation](VALIDATION.md), [security boundaries](SECURITY.md) and
[contribution checks](CONTRIBUTING.md) before changing or distributing the plugin.
The initial review findings and regression evidence are mapped in
[review fixes](REVIEW-FIXES.md).
