# Integration requirements

Omachines currently adapts an existing Omarchy Windows setup. Optional bridges
are external prerequisites, not programs that this repository installs. The
installer never provisions Windows, credentials, storage or SSH authentication.

| Capability | Expected local integration |
| --- | --- |
| Start/stop | Packaged `/usr/bin/omarchy-windows-vm` with the privileged `__priv up/down` interface. User authorizes elevation in a visible terminal. |
| Viewer | Executable `~/.local/bin/windows-vm-launch`; starts/opens the viewer, retains the VM when the viewer closes. Existing window is matched by FreeRDP class and title `Windows VM - Omarchy`. |
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
