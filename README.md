# Omachines

A native Omarchy bar panel for an existing Windows VM: desktop controls, agent
connections, named backups, recovery and CPU/RAM allocation.

**Early preview:** v0.2 supports one existing Omarchy Windows setup. It does not
install Windows or create other VMs. Optional SSH, Cua and Aside bridges must
already be configured. Linux providers and a VM creation wizard are future work.

## Use

Click **VM** on the bar. Right-click opens or focuses Windows; middle-click checks
connections. The existing viewer wrapper determines what happens when it closes.

- **Overview:** desktop, storage, Aside, shared files, start and shutdown.
- **Agents:** separate SSH, desktop, Cua, Aside browser, CLI version and CLI sign-in
  checks; console recovery, connection instructions and private diagnostics.
- **Backups:** name a backup, see size and verification time, recheck integrity,
  test an offline restored copy, or deliberately restore Windows.
- **Settings:** CPU/RAM allocation, Aside integration, notifications, check interval
  and recent operation history.

Arrows navigate; Enter activates; 1–4 select a section; R checks connections;
Escape closes. Tab enters the backup or resource form, where normal editing keys
apply. Disruptive actions have a confirmation with **Cancel** initially focused.
Actual restore also requires typing the selected backup identity in its terminal.

## Install and update

Tested on Omarchy 4.0.6 with its Quickshell shell, Python 3, Docker and an existing
Omarchy Windows VM using protected storage mounts. No pip/npm packages, separate
web server, root daemon or new passwordless policy are installed.

```bash
python3 scripts/check.py
python3 install.py
# Optional, required for the built-in backup/recovery/resource controls:
sudo /usr/bin/python3 -I scripts/install-admin.py
# Refresh the catalogue from Agents or Backups after installing the helper.
```

Both commands are repeated when updating code that changes the administrator
helper. Read the source before installing privileged code. The helper is copied
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

The versioned machine registry and bounded history live in
`~/.local/state/omachines/`. Registry migration preserves existing data. Unsupported
providers or connection changes fail closed. This release describes the existing
machine; it does not yet expose arbitrary connection editing or multiple VMs.
See [integration requirements](INTEGRATION.md) for the optional bridges.

## Backups and recovery

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

## Aside and agents

Omachines distinguishes browser tool availability from CLI installation and account
sign-in. A signed-in CLI does not prove model access, subscription or remaining usage;
checks do not start an AI task. The supplied CLI resolver supports Aside's versioned
Windows installation and updater junction. Omachines does not change Aside's remote
debugging or remote-session settings, migrate bookmarks, or inspect browser pages.
Agents using broad guest access can reach accounts signed into that Windows user.

## Performance, theme and validation

One shared service serves every monitor. The standard-library backend polls local
reachability every 15 seconds and checks the guest every 120 seconds by default.
Probes have time and output limits. There is no continuous screenshot stream or
invented agent activity. Status can be stale or unknown.

Native `qs.Ui`, popup colours, fonts, spacing and controls follow Omarchy's theme.
The panel stays compact and scrolls when needed. Playwright cannot test this native
QML surface; the check script runs Python tests, manifest validation, qmllint and
real Qt key/mouse interactions in an isolated offscreen Quickshell runtime.
Physical Hyprland and live Windows checks are recorded separately.

See [validation](VALIDATION-v0.2.md), [security boundaries](SECURITY.md),
[contribution checks](CONTRIBUTING.md), and the
[Omarchy plugin contract](https://plugins.omarchy.org/develop.html).
