# Version 0.3 validation — 26 October 2026

Version 0.3 adds the Omarchy VM (`omarchy-vm`, provider `omarchy-qemu`) beside the
existing Windows VM, a machine switcher, and a fix for Windows backups after a host
restart. The administrator helper is unchanged (still 0.2.0); no helper reinstall
is needed.

## Automated checks

On Omarchy 4.0.6 with Quickshell 0.3.2, `python3 scripts/check.py` passed:
129 Python tests, `omarchy plugin validate`, zero-warning qmllint, and 25 native
Qt interaction cases (26 passes including initialization) in the isolated
offscreen Quickshell runtime. The Python suite also passed with an empty `HOME`
(no wrappers and no `omarchy-vm`), approximating generic CI. The repository
privacy heuristics reported no findings. Gitleaks was not installed locally and
was not run; CI runs it on the pull request.

New Python coverage:

- Registry schema 2: v0.2 migration keeps the Windows entry byte-for-byte, adds
  the Omarchy VM, keeps `machines-v1.json` and writes only once; missing known
  entries are added; changed paths/ports, unknown providers, duplicate identities,
  unknown selection and unknown schema fail closed without probes and preserve the
  file; selection persists, requires an installed `omarchy-vm`, falls back to
  Windows when it disappears, and is refused during maintenance.
- Omarchy status: only known fields are kept (install-media path becomes a
  boolean, unsafe guest strings are dropped); state mapping for stopped,
  starting, running, paused and error; an Omarchy backup changes only the Omarchy
  summary; guest-probe parsing; Cua `health_report` structured content (the real
  shape: `schema_version` "1", `overall`, `checks[]`).
- Omarchy collector: deep checks map to the existing `ssh`/`desktop`/`cua` checks
  and resources; Cua is skipped without a desktop session; SSH failures never leak
  output; a stopped VM is offline without probes; shallow polls run only
  `status --json` and keep the previous evidence; local labels override manifest
  labels; backup paths are never exposed; Windows-only capabilities are false.
- Snapshots: both machine summaries with an `active` flag; an unavailable
  summary does not break the selected snapshot; evidence from another machine or
  a placeholder is never reused; per-machine history; Windows still reports
  "stopped" after a later Omarchy operation.
- Daemon: `select` emits a placeholder and stale results are discarded.
- Operations: Omarchy start is a background worker and restore uses a terminal;
  Windows-only actions and Windows backup identities are refused for the Omarchy
  VM (and `omarchy-` identities for Windows) without spawning; a confirmation for
  another machine is refused; workers stay pinned to the requested machine and
  fail closed if it disappears; backup stages and completion are parsed and
  failures are reduced to short path-free reasons; restore requires the exact
  typed identity of a known backup; allocation limits; local labels; an existing
  viewer is focused instead of launching another; the connection guide.
- Windows storage anchor: a missing anchor starts Windows, waits, shuts it down
  and then backs up (in that order) and reports that Windows was left stopped; a
  mounted anchor, a running Windows or an unsupported layout uses the normal
  path; not ready or still unmounted shuts Windows down and backs up nothing;
  cancelled authorization changes nothing; the readiness wait is bounded.

New native Qt cases: switcher hidden with one machine (M ignored), switcher click
and M key select the other machine, switching clears an open confirmation,
Omarchy hides Windows-only actions/buttons and Windows wording, Windows keeps its
controls, Omarchy confirmations name the machine with Cancel focused first and
pin its identity, an operation of the other machine is named, and the bar text
and tooltip. Measured in the harness, the switcher with the longest state labels
needs 376 of the panel's 430 px.

## Read-only probes of the running Omarchy VM

Before installation, only read-only commands were run against the VM:
`omarchy-vm status --json` (about 0.07 s), `backups --json`, `resources`, one
`omarchy-vm-ssh` probe (about 0.15 s) and `health_report` through
`omarchy-vm-mcp` (about 0.5 s). The working-tree backend's
`omachinesctl status --deep --machine omarchy` reported "Omarchy VM is ready",
6 CPUs, about 7.7 GiB RAM, root filesystem free space, a running Hyprland session,
Cua ready, allocation 6 CPUs/8G and the existing verified backup, in 0.84 s.

Not done: no start, stop, backup, verify, restore, resource change or viewer
against the Omarchy VM; Windows was not started or stopped; neither installer was
run; the physical panel was not viewed.

## Live desktop checks — to be completed after `python3 install.py`

> **Not yet performed.** Record results here. Ask before anything that stops or
> restarts a VM; do not restore over a VM without explicit approval.

1. **Install and migration.** `install.py` succeeds; `machines.json` has schema 2
   with the unchanged Windows entry and `machines-v1.json` exists;
   `omachinesctl machines` lists both.
2. **Bar.** With the Omarchy VM running and Windows stopped, the label shows
   `VM` plus the Arch-logo glyph (check it renders in the theme font); the tooltip
   names both machines; vertical bar / hidden label variants are sensible.
3. **Switcher.** Visible at the top of the panel on every monitor, fits the
   narrowest display, click and **M** switch machines, the panel first shows
   "Checking Omarchy VM" then the deep result; the choice persists across panel
   reopen, shell reload and monitors.
4. **Omarchy sections.** Overview (guest disk, VM drive, wording), Agents (only
   Commands/Desktop/Cua; guide names `omarchy-vm-ssh`/`omarchy-vm-mcp` and guest
   sudo), Backups (existing backup listed with "Restore tested"; no rehearsal or
   catalogue controls), Settings (allocation 6/8, limits 16 CPUs).
5. **Non-disruptive actions.** Open desktop focuses an existing viewer and opens
   one if none; Check connections; connection guide; diagnostic report; name a
   backup; Verify integrity runs in the background and records history.
6. **Restore cancel path only.** Start Restore, confirm, then type a wrong value
   in the terminal: it must report cancellation and leave the VM unchanged.
7. **With approval:** Shut down / Start in background; Create complete backup
   (about 40 s, stages shown, VM restarts); an allocation round trip.
8. **Cross-machine state.** While an Omarchy operation runs, the Windows view says
   "In progress: Omarchy VM: …" and its actions are disabled.
9. **Windows unchanged.** Windows view keeps Aside, console, handoff, catalogue,
   rehearsal and admin-backed controls and wording.
10. **Windows anchor fix (with approval, after a host restart, Windows stopped).**
    Create a Windows backup: the terminal explains the missing storage, starts
    Windows, waits for SSH, shuts it down, backs up; Windows ends stopped and the
    new backup is verified.
11. **Backend cost.** One backend for all monitors; sample CPU/RSS with the
    Omarchy VM selected.

## Known limitations

- One operation runs at a time across both machines (shared reservation and lock).
- `omarchy-vm view` focuses an existing viewer with `hyprctl dispatch
  focuswindow`; Omachines focuses an existing viewer itself first using the same
  dispatch as for Windows.
- Omarchy backup labels live in Omachines' private state, not in the backup
  manifest, so `omarchy-vm backups` still shows the original label.
