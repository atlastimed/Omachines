# Omachines

A native Omarchy bar panel for existing virtual machines: desktop controls, agent
connections, named backups, recovery and CPU/RAM allocation.

**Early preview:** v0.3 manages two existing machines: an Omarchy Windows VM and
an Omarchy (Linux) VM run by the user-level `omarchy-vm` command. It does not
install either guest or create other VMs. Optional SSH, Cua and Aside bridges must
already be configured. A VM creation wizard and arbitrary providers are future work.

## Use

Click **VM** on the bar. The label adds a logo for each running VM (the Windows
logo, or the Arch logo for the Omarchy VM) and the tooltip lists every machine.
Right-click opens or focuses the selected machine; middle-click checks connections.
Closing a viewer keeps its VM running.

When both machines are available, a switcher at the top of the panel selects the
machine shown; **M** cycles machines. The choice is remembered and shared by every
monitor. Controls that a machine does not support are hidden. Only one operation
runs at a time across both machines; the panel names the machine an operation
belongs to.

- **Overview:** desktop, storage, Aside (Windows), shared files, start and shutdown.
- **Agents:** separate SSH, desktop and Cua checks; for Windows also Aside browser,
  CLI version and CLI sign-in checks and console recovery; connection instructions
  and private diagnostics.
- **Backups:** name a backup, see size and verification time, recheck integrity,
  test an offline restored copy (Windows), or deliberately restore the machine.
- **Settings:** CPU/RAM allocation, Aside integration, notifications, check interval
  and recent panel operation history.

Arrows navigate; Enter activates; 1–4 select a section; M switches machine;
R checks connections; Escape closes. Tab enters the backup or resource form, where
normal editing keys apply. Disruptive actions have a confirmation with **Cancel** initially focused.
Actual restore also requires typing the selected backup identity in its terminal.

## Install and update

Tested on Omarchy 4.0.6 with its Quickshell shell, Python 3, Docker and an existing
Omarchy Windows VM using protected storage mounts, plus an Omarchy VM run by
`~/.local/bin/omarchy-vm` (QEMU/KVM as a systemd user unit). The Omarchy VM
appears only when that command exists. No pip/npm packages, separate web server,
root daemon or new passwordless policy are installed.

```bash
python3 scripts/check.py
python3 install.py
# Optional, required for the built-in backup/recovery/resource controls:
sudo /usr/bin/python3 -I scripts/install-admin.py
# Refresh the catalogue from Agents or Backups after installing the helper.
```

Both commands are repeated when updating code that changes the administrator
helper (v0.3 does not change it). The Omarchy VM provider needs no helper. Read the source before installing privileged code. The helper is copied
root-owned into `/usr/local/lib/omachines/`; old helper versions are retained there.
Each administrative operation still asks for authorization in a visible terminal.
The prompt asks for the **Omarchy password**.

The user installer copies an explicit file list into
`~/.config/omarchy/plugins/atlastimed.omachines/`. Content-addressed QML paths avoid
stale component caches. It preserves settings, unrelated widgets and previous
releases under `~/.local/state/omachines/install-backups/`, and hot-reloads without
restarting the shell or Windows. An operation/maintenance gate prevents overlap
with plugin workers. Malformed or symlinked shell settings are rejected.

```bash
# Remove the panel; preserve VM, backups, settings and helper:
python3 install.py --uninstall
# Optional compatibility with a previously reviewed v0.1 backup workspace:
python3 install.py --setup-workspace /absolute/path/to/WindowsVM
```

The versioned machine registry (schema 2), selected machine and bounded history
live in `~/.local/state/omachines/`. Migration from the v0.2 registry keeps the
Windows entry unchanged, adds the Omarchy VM and retains the old file as
`machines-v1.json`. Each provider has fixed connection paths; unsupported providers
or changed connections fail closed. `omachinesctl select <windows|omarchy>` and
`omachinesctl machines` select and list machines from a terminal. See
[integration requirements](INTEGRATION.md) for each provider's contract.

## Backups and recovery

### Windows

The built-in administrator helper checks storage identity and capacity, takes
Omarchy's lifecycle lock, stops Windows, copies its disk/TPM/firmware and private
compose configuration, validates the archive and SHA-256, and starts Windows if
it was running. Shared-folder files are separate. Backups are stored under
`~/Windows-VM-Backups/`; ensure that location is on encrypted storage. Existing
backups and incomplete work are retained. The panel's labels do not rename files.

**Verify integrity** reads the entire archive again. **Test an offline restored
copy** verifies and extracts it into a separate root-protected directory under
`$HOME/../.omachines-rehearsals/`. The disposable container has no network, published
ports or host shares and uses 2 CPUs/4 GiB RAM. Open its private boot image, then
record whether Windows reached the desktop or sign-in screen. Recording the result
stops that copy; its files remain for inspection. A checksum and a reviewed boot
are separate pieces of evidence. This is not a full application-level restore test.

**Restore Windows** verifies the selected archive afresh and checks space for a
copy alongside the existing storage. After typed confirmation it restores disk,
TPM and firmware together. It retains the previous storage and journals the swap
for recovery. The panel can return to that retained storage after another explicit
confirmation. Current host connections and CPU/RAM allocation stay in place;
private compose files in the backup are retained for manual recovery, not applied
automatically. Guest passwords and applications revert to their backed-up state.

Complete backups and rehearsal copies include signed-in browser state. They must
remain private. Restores need enough free space to retain both copies. This release
never automatically prunes backups or deletes VM data. Do not manually manipulate
Docker containers or storage during an operation. Inspect any interrupted operation
before retrying; a stopped VM may need starting again.

CPU/RAM changes check host capacity and available memory before shutdown, preserve
unrelated compose settings, and restart Windows only if it was running. The original
compose file is retained. If container startup fails, the helper attempts to recover
the previous allocation. Disk resizing is outside this version.

After the host restarts, Omarchy attaches the protected Windows storage only when
Windows starts. A backup requested while Windows is stopped therefore starts
Windows in the same terminal, waits until it answers over SSH (at most five
minutes), shuts it down cleanly, then runs the backup. Windows stays stopped, as it
was. If Windows does not finish starting, it is shut down and nothing is backed up.

### Omarchy VM

`omarchy-vm backup` stops the VM if it is running, archives its disk and firmware
variables with its host-side configuration (never its private SSH key) into
`~/Omarchy-VM-Backups/<id>/`, verifies the SHA-256 and contents, and restarts it
if it was running. No password is needed, so it runs in the background and the
panel shows its stages. Keep that folder on encrypted storage. **Verify integrity**
rechecks a backup; names given in the panel are stored privately by Omachines and
do not change the backup's own manifest.

**Restore Omarchy VM** opens a terminal that asks you to type `RESTORE <id>`. It
then verifies the archive, stops the VM, keeps the current disk aside in the VM
storage directory as `previous-<time>/`, extracts the backup and restarts the VM if
it was running. Shared-folder files are separate. CPU/RAM changes are saved by
`omarchy-vm resources` and restart a running VM; the panel limits them to 16 CPUs
and leaves at least 4 GiB of host RAM.

## Agents

The connection guide names each machine's existing bridges. For the Omarchy VM,
`omarchy-vm-ssh '<command>'` runs a command in the guest and `omarchy-vm-mcp` is
Cua Driver's stdio MCP in its auto-signed-in Hyprland session. Agents get
passwordless sudo inside that guest (not on the host) and can read and write the
shared folder `~/OmarchyVM` (`~/Shared` in the guest). Checks report SSH, the
desktop session and Cua health; they never start an agent task.

## Aside (Windows)

Omachines distinguishes browser tool availability from CLI installation and account
sign-in. A signed-in CLI does not prove model access, subscription or remaining usage;
checks do not start an AI task. The supplied CLI resolver supports Aside's versioned
Windows installation and updater junction. Omachines does not change Aside's remote
debugging or remote-session settings, migrate bookmarks, or inspect browser pages.
Agents using broad guest access can reach accounts signed into that Windows user.

## Performance, theme and validation

One shared service serves every monitor. The standard-library backend polls local
reachability every 15 seconds and checks the selected guest every 120 seconds by
default. For the Omarchy VM the 15-second poll runs only `omarchy-vm status --json`;
the other machine gets the same lightweight poll for the switcher and bar.
Probes have time and output limits. There is no continuous screenshot stream or
invented agent activity. Status can be stale or unknown.

Native `qs.Ui`, popup colours, fonts, spacing and controls follow Omarchy's theme.
The panel stays compact and scrolls when needed. Playwright cannot test this native
QML surface; the check script runs Python tests, manifest validation, qmllint and
real Qt key/mouse interactions in an isolated offscreen Quickshell runtime.
Physical Hyprland and live Windows checks are recorded separately.

See [validation](VALIDATION-v0.3.md) ([v0.2](VALIDATION-v0.2.md)), [security boundaries](SECURITY.md),
[contribution checks](CONTRIBUTING.md), and the
[Omarchy plugin contract](https://plugins.omarchy.org/develop.html).
