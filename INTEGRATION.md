# Integration requirements

Omachines adapts two existing machines: an Omarchy Windows setup (provider
`omarchy-windows`) and, from v0.3, an Omarchy VM run by `omarchy-vm` (provider
`omarchy-qemu`). Optional bridges are external prerequisites, not programs that
this repository installs. The installer never provisions a guest, credentials,
storage or SSH authentication.

## Omarchy Windows provider

| Capability | Expected local integration |
| --- | --- |
| Start/stop | Packaged `/usr/bin/omarchy-windows-vm` with the privileged `__priv up/down` interface. User authorizes elevation in a visible terminal. |
| Viewer | Executable `~/.local/bin/windows-vm-launch`; starts/opens the viewer, retains the VM when the viewer closes. Existing window is matched by FreeRDP class and title `Windows VM - Omarchy`. Opening it keeps the operation (and its lock) until that window appears or the launcher exits; a launcher still waiting after 10 minutes, typically at an unanswered authorization prompt, is stopped with its process group. The viewer then runs on its own. |
| Command checks | Executable `~/.local/bin/windows-vm-ssh`; passes its argument to a preconfigured guest SSH connection without prompting. Loopback port 2222 is used for reachability. |
| Desktop tools | Executable `~/.local/bin/windows-vm-mcp`; exposes the guest's interactive Cua driver as JSON-line stdio MCP, including `health_report` and `launch_app`. |
| Aside tools | Executable `~/.local/bin/windows-vm-aside-mcp`; exposes Aside's `repl` through stdio MCP. A health probe only prints a fixed connection-check message. |
| Console recovery | Omarchy's local web console at `http://127.0.0.1:8006`. |
| Console handoff | Existing guest script `C:\OEM\guest-console.ps1`, invoked through the SSH bridge. It should transfer a disconnected session, not unlock a locked user. |
| Shared files | Existing `~/Windows` directory. |
| Storage free space | Existing `~/.windows` storage path. |

The guest must already have PowerShell, working SSH, an interactive desktop and
the relevant optional tools. Cua running in a non-interactive service session is
not a substitute for an interactive desktop. Aside is currently detected under
the connected Windows user's local application directory.

## Existing backup provider

The optional `setupWorkspace` setting selects a **trusted local administrative
workspace** containing `create-development-backup.sh`. Omachines invokes it with
`sudo bash` in a visible terminal after confirmation. Do not point this setting
at a shared/downloaded/guest-controlled directory.

The tested provider validates its dedicated storage mounts and available backup
space, performs a graceful shutdown, takes Omarchy's root-owned lifecycle lock,
copies disk/TPM/firmware and private configuration to a separate encrypted disk,
verifies the archive and checksum, releases the lock, and starts Windows again.
It attempts recovery on failure and retains incomplete output for inspection.

Current compatibility contract:

- Source includes `OMACHINES_PROGRESS_FD` and `backup_copy_lock` markers. These are
  compatibility checks, **not** proof that arbitrary code is safe or locking is correct.
- Emits `OMACHINES_STAGE:` followed by `preflight`, `stopping`, `copying`,
  `verifying`, `starting`, or `complete` on stdout.
- Returns nonzero on failure; returns zero only after verification and restart.
- Writes an absolute backup directory to `research/development-ready.path` and
  `complete` to `research/development-ready.status` on success.
- Uses a directory name `development-ready-YYYYMMDDTHHMMSSZ`.

Imported baseline history can use the existing `research/baseline.path/status`
and `research/development-validation.json` completion records. Merely finding a
backup directory is not considered successful verification. These records are
trusted local evidence, not a cryptographic guarantee against tampering.

A portable backup implementation, arbitrary VM connection configuration, and
restoration UI are intentionally outside v0.1. This protocol is documented so
reviewers can assess the boundary; it is not a recommendation to run an
unreviewed privileged script.

## Version 0.2 administrator provider

When installed, the root-owned `backend/admin.py` supersedes the v0.1 backup
workspace for backup creation. The old protocol above remains a compatibility
fallback and source of imported evidence. v0.2 requires Omarchy's root-owned
compose at `/var/lib/omarchy/windows/docker-compose.yml`, its protected per-user
storage bind mount, the standard container identity, and storage named `storage`
below a root-owned parent. An unsupported layout is refused, never migrated by
this plugin. No Docker-group membership is needed.

The helper accepts only fixed actions and validated backup identities or integer
allocations. It runs via `sudo /usr/bin/python3 -I`, never imports user-controlled
modules, serializes root operations, and uses Omarchy's existing lifecycle lock.
Root-owned per-user metadata is readable by that user's primary group. The
root-owned backup archives remain private. Metadata includes verification time,
size, allocation and a distinct offline-boot result, not credentials.

The versioned user registry describes the existing loopback ports and wrappers.
A provider identifier is an explicit boundary for future adapters, not evidence
that a Linux adapter or VM creation is already implemented.

### Aside CLI updates

`backend/aside-cli.ps1` returns only installed/version/sign-in fields in its default
mode. The Windows SSH bridge must run PowerShell with that user's local application
directory available. `-Mode mcp` starts the installed Aside MCP server. If an updater
junction cannot be traversed from elevated SSH, the resolver accepts only a target
inside Aside's own version directory with a known numeric version layout.

An existing host Aside MCP wrapper may invoke a reviewed copy of this script in
the guest. Installation of that copy and SSH authentication remain setup tasks;
the plugin installer does not silently rewrite existing host bridges or guest files.
The script must be kept in sync if used by an external wrapper. Neither MCP health
nor CLI account status launches a model-backed task or proves model entitlement.

## Version 0.3 Omarchy VM provider (`omarchy-qemu`)

The machine `omarchy` uses fixed paths. They always come from Omachines' own
definition: a stored registry is rewritten to it on load, keeping only the selection
and a valid display name, and unknown providers or identities fail closed. It is
listed only while `~/.local/bin/omarchy-vm` is an executable file.
Everything runs as the desktop user: no root, sudo, polkit or terminal password.

| Capability | Expected local integration |
| --- | --- |
| Status | `omarchy-vm status --json` prints `{"running", "unitState", "maintenance", "guest", "sshReady", "viewerOpen", "installMedia", "diskUsedBytes"}` quickly, without SSH. `guest` is QEMU's run state (`running`, `paused`, ...) or `null`; `sshReady` means an SSH banner from the guest's sshd over vsock (context ID 2223, port 22). `unitState` is the unit's systemd `ActiveState`; `deactivating` shows as shutting down and `failed` as stopped unexpectedly. `maintenance` is `null` or the lifecycle command holding omarchy-vm's lock (`backup`, `restore`, `resources`, `create`, `stop`). Only these fields are read, unknown values count as absent, and the install-media path is reduced to a boolean. |
| Lifecycle lock | `backup`, `restore`, `resources`, `create` and `stop` hold `$XDG_RUNTIME_DIR/omarchy-vm/lifecycle.lock`; `start`, `view` and the unit itself refuse with `omarchy-vm: maintenance in progress (<name>); try again when it finishes`. When one runs that no Omachines operation started, the panel shows "<name> in progress outside the panel", disables the same controls as during its own operations and skips the guest probes; when it ends, a deep check runs at once. `start` and `view` also refuse when less memory is free than the VM's allocation. |
| Start/stop | `omarchy-vm start` starts the systemd user unit `omarchy-vm.service`; `omarchy-vm stop` shuts the guest down cleanly over SSH with an ACPI fallback, and prints `Forced off: the guest did not shut down cleanly` before `Omarchy VM is stopped` when QEMU had to be stopped instead. The panel reports that as a forced stop and never uses `stop --force`. |
| Viewer | `omarchy-vm view` starts the VM if needed and opens a viewer (Hyprland class `remote-viewer`, title starting `Omarchy VM`). Omachines focuses an existing viewer itself. Closing it leaves the VM running. |
| Backups | `omarchy-vm backup` stops a running VM, writes `~/Omarchy-VM-Backups/<id>/{vm.tar,SHA256SUMS,manifest.json}`, verifies it and restarts the VM if it was running. Progress lines `OMACHINES_STAGE:copying`, `verifying`, then `Backup <id> saved and verified`, then optionally `OMACHINES_STAGE:starting`. Nonzero exit on failure, with `omarchy-vm: <reason>` on stderr. A nonzero exit after the saved line means only the restart failed (`omarchy-vm: backup saved, but the VM did not start again: <reason>`). The manifest's `cleanShutdown` is false when the VM was forced off first. Identities match `omarchy-\d{8}T\d{6}Z`. |
| Catalogue | `omarchy-vm backups --json` lists `{id, label, createdAt, bytes, verified, verifiedAt, restoreTested, ...}`. Omachines keeps only those fields, never the path. |
| Verify/restore | `omarchy-vm verify <id>`; `omarchy-vm restore <id> --replace-current` verifies and extracts beside the running VM, then stops it and swaps the files under a journal (`restore-journal.json` in the VM storage directory, which blocks starts if a crash interrupts the swap). The current disk is kept as `previous-<time>/`. `OMACHINES_STAGE:restored` follows the swap; the VM restarts only if it was running (`OMACHINES_STAGE:starting`). A nonzero exit after `restored` means only the restart failed (`omarchy-vm: restored, but the VM did not start again: <reason>`); before it, the current disk is unchanged. |
| CPU/RAM | `omarchy-vm resources` prints `{"cpus", "memory", "hostCpus"}`; `resources --cpus N --memory NG` saves the allocation and restarts a running VM. It refuses, before changing anything, a size larger than the memory free plus what the running VM gives back; if the restart still fails, it restores the previous allocation, starts the VM with it and exits nonzero. The tool accepts at most 16 CPUs and `99G`, leaving 4 GiB for the host. |
| Command checks | `~/.local/bin/omarchy-vm-ssh '<command>'` runs one command as the guest user over vsock, with BatchMode and a pinned host key. One read-only probe returns CPU count, memory, root filesystem size/free, and whether `systemctl --user show-environment` contains `HYPRLAND_INSTANCE_SIGNATURE=` (the auto-signed-in Hyprland session). |
| Desktop tools | `~/.local/bin/omarchy-vm-mcp` is Cua Driver's stdio MCP in that session. `health_report` must return `structuredContent` with `schema_version` `"1"` and `overall` `"ok"`, the same schema as on Windows. |
| Shared files | `~/OmarchyVM` on the host; the guest sees it as `~/Shared`. |
| Storage | VM storage `/mnt/windows-vm/omarchy` (free space is shown); configuration `~/.config/omarchy-vm/config.json` (read only through `omarchy-vm`). |

The 15-second poll runs only `status --json`. The deep check (every check interval,
or **Check connections**) adds the SSH probe, then Cua health when the desktop
session is up. After SSH comes back, deep checks repeat about every 20 seconds for
up to 5 minutes until the desktop and Cua are ready, so a guest still signing in
does not wait for the next interval. Omarchy VM commands run in their own session,
detached from the operation terminal: closing it or pressing Ctrl+C there cannot
interrupt them. The catalogue and allocation are read on deep checks. Aside, the
recovery console, console handoff, the administrator helper and offline-boot
rehearsal do not exist for this provider; its capabilities hide them.

Only restore uses a visible terminal, for the typed `RESTORE <id>` confirmation.
Start, stop, backup, verify, label and allocation are background workers that
record progress in the operation state. Backup labels are stored privately in
`~/.local/state/omachines/labels.json`; the backup manifest is never rewritten.

## Version 0.3 Windows storage anchor

The root helper requires Omarchy's protected bind mount at
`/var/lib/omarchy/windows/mounts/users/<uid>/storage`. Omarchy recreates it only in
`omarchy-windows-vm __priv up`, so after a host restart it is missing until Windows
first starts. When a Windows backup is requested while Windows is stopped and that
anchor is not a mountpoint, the operation terminal explains this, runs
`__priv up` (sudo), waits for the guest to answer over SSH (at most five minutes,
or two minutes without an SSH bridge), runs `__priv down` (the bind stays mounted),
then runs the helper's backup with Windows stopped. Windows is left stopped. If it
does not finish starting it is shut down and nothing is backed up. The helper
itself is unchanged.
