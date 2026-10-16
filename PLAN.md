# Omachines 0.1

Omarchy plugin for an existing Windows VM. Public early preview; no VM reprovisioning.

## Deliverable

- Native themed bar widget and keyboard-friendly panel; one service across monitors.
- Open/focus viewer, background start, graceful shutdown with confirmation, recovery console and shared-folder shortcuts.
- Independent VM, SSH, interactive desktop, Cua and optional Aside checks with honest stale/unknown states.
- Complete cold-backup creation in a visible terminal, durable progress and verification, backup history; retain existing backups.
- Native settings for check interval, notifications, optional Aside and connection locations. CPU/RAM editing, restore, other VM providers and AI task launching deferred.
- Local installer/update/removal preserving the VM, backups, credentials and unrelated shell settings.

## Architecture

QML uses installed qs.Ui, qs.Commons Style/Color and the shell's service registry. A small Python standard-library backend owns shared polling, typed state, bounded probes and command dispatch. No Docker-group access, new network listener, root daemon or new credential store. Reuse the existing optional Windows SSH/Cua/Aside wrappers. Separate the Omarchy Windows adapter from presentation for future machine types.

Passive checks never ask for authentication. Actions that need it use Omarchy's existing authorization or an explicit terminal. Lifecycle/backup operations survive a bar reload and reject overlap. Errors and diagnostics exclude remote command output and credentials. No invented percentages or agent activity.

## Validation

1. Unit tests for state transitions, stale probes, command allowlists, safe backup paths, lock/recovery behaviour and credential-free reports.
2. Manifest validation, qmllint and Qt Quick interaction tests with deterministic fixtures, including light/dark theme tokens.
3. Read-only checks against the running guest; live panel screenshots on physical Hyprland, keyboard/multi-monitor checks and idle-resource sampling.
4. Obtain fresh user go-ahead before a real shutdown or cold-backup test. Test the authorized path and verify Windows/agents recover.

## Tooling

Playwright is bundled with Codex and was already used to validate the design preview. It tests web content, not the native QML panel. Use installed Qt tools for QML, Hyprland/Omarchy IPC and screenshots for the actual Linux surface, and existing Cua tools for the Windows guest. Inspect capabilities before adding any packages or MCP registrations.

## Progress

- [x] Scope and initial architecture recorded.
- [x] Backend and operations implemented.
- [x] Native panel and settings implemented.
- [x] Automated checks and local installation.
- [x] Physical desktop and approved VM lifecycle/backup validation.
- [x] Documentation and local first-version validation complete.
- [x] Public repository, privacy/secret checks and initial implementation PR.
- [x] Assess the initial review and address R1–R7 in the agreed single fix round.
- [ ] Merge decision after the fix-round checks and handoff.
