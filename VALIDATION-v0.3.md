# Version 0.3 validation — 26 October 2026

Version 0.3 adds the Omarchy VM (`omarchy-vm`, provider `omarchy-qemu`) beside the
existing Windows VM, a machine switcher, and a fix for Windows backups after a host
restart. The administrator helper is unchanged (still 0.2.0); no helper reinstall
is needed.

## Automated checks

On Omarchy 4.0.6 with Quickshell 0.3.2, `python3 scripts/check.py` passed:
132 Python tests (129 at first, plus 3 added for the review fix below), `omarchy plugin validate`, zero-warning qmllint, and 25 native
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

Review fix (Codex, PR #3): if `omarchy-vm status --json` fails while the Omarchy VM
is selected, the provider now reports "Omarchy VM status is unavailable" as that
machine's state. Before, the whole snapshot failed, and the panel fell back to one
without the machine list or the switcher. Any other failed collection is now
attributed to the selected machine, so the daemon no longer discards it as stale
and retries without pause. Covered by three new tests, which fail without the fix.

## Read-only probes of the running Omarchy VM

Before installation, only read-only commands were run against the VM:
`omarchy-vm status --json` (about 0.07 s), `backups --json`, `resources`, one
`omarchy-vm-ssh` probe (about 0.15 s) and `health_report` through
`omarchy-vm-mcp` (about 0.5 s). The working-tree backend's
`omachinesctl status --deep --machine omarchy` reported "Omarchy VM is ready",
6 CPUs, about 7.7 GiB RAM, root filesystem free space, a running Hyprland session,
Cua ready, allocation 6 CPUs/8G and the existing verified backup, in 0.84 s.

At that stage there was no start, stop, backup, verify, restore, resource change
or viewer against the Omarchy VM (all done afterwards; see the live checks),
Windows was not started or stopped, neither installer had run, and the physical
panel had not been viewed.

## Live desktop checks — 26 October 2026, after `python3 install.py`

Run on the physical desktop (three monitors: 2× 2560×1440, 1× 1920×1080) with the
Omarchy VM running and Windows stopped. VM operations were requested through
`omachinesctl action … --machine omarchy`, the same request path the panel uses,
and followed in `operation.json`. Only the Omarchy VM was stopped or started.

1. **Install and migration — passed.** `install.py` succeeded; `machines.json` is
   schema 2 with the Windows entry unchanged, `machines-v1.json` holds the
   original, and `omachinesctl machines` lists both (Windows "stopped", Omarchy VM
   "running").
2. **Bar — passed (horizontal bar).** All three bars show `󰍹 VM` followed by the
   Arch-logo glyph, which renders in the theme font. Tooltip and vertical-bar
   variants were not viewed.
3. **Switcher — partly checked.** The panel opened with `omarchy-shell shell
   summon` shows both chips ("Windows 11 · Off", "Omarchy VM · Running") within
   the panel width. `omachinesctl select omarchy` persisted in `machines.json` and
   the open panel switched at its next 15-second poll (a selection made outside the
   panel is not pushed immediately). Clicking a chip and **M** were not exercised
   live (no input was injected into the desktop); the native Qt cases cover them.
4. **Omarchy Overview — passed.** Name, Arch logo, "6 CPUs · 7.7 GiB RAM",
   "Omarchy VM is ready", Hyprland desktop session available, guest disk free
   83.9 GiB, VM drive free 171.2 GiB, actions Open desktop / Shared folder / Start
   in background (disabled while running) / Shut down. No Aside entry. The Agents,
   Backups and Settings tabs were not viewed live; their content is covered by the
   native Qt cases and by the backend results below.
   *Fix made during this check:* a finished Windows operation ("Windows 11:
   Windows shut down") was shown on the Omarchy page. Another machine's operation
   is now shown only while it runs or when it failed or was interrupted (test added
   to `test_operation_of_other_machine_is_named`).
5. **Non-disruptive actions — passed.**
   - Deep check: ssh ready, desktop available, Cua ready, 0.85 s.
   - Open desktop with no viewer: started `remote-viewer` ("Omarchy VM viewer
     requested"); with a viewer behind another window: "Omarchy VM viewer focused"
     and the viewer became the active window.
   - Verify integrity: background worker, 12 s, "Backup checksum and contents
     verified".
   - Name a backup: "Backup name saved"; the panel data shows the new name while the
     backup manifest keeps its own label.
   - Diagnostic report (scratch state folder): only version, machine, provider,
     state, checks, resources, capabilities and counts; no paths or guest output.
   - Connection guide text names `omarchy-vm-ssh`, `omarchy-vm-mcp`, `omarchy-vm`,
     guest sudo and the shared folder.
6. **Restore cancel path — passed (without the terminal).** The provider's restore
   step, run in a scratch state folder with the wrong typed value, printed the
   backup summary and returned "Restore cancelled; Omarchy VM is unchanged"; the VM
   kept running and its disk was untouched. The terminal launch itself was not
   exercised.
7. **Lifecycle, backup, allocation — passed.**
   - Shut down: 3 s, "Omarchy VM shut down"; Windows still reported "stopped".
   - Allocation while stopped: 4 CPUs/6 GiB saved, then 6/8 restored; 40 CPUs
     refused with "Choose 1-16 CPUs and 4-27 GiB RAM".
   - Start in background: complete in 1 s; SSH ready after about 10 s; deep check
     ready with allocation 6/8.
   - Create backup: 56 s with stages Shutting down → Copying → Verifying → done;
     `omarchy-20261026T213051Z` (9.7 GiB) saved and verified, VM started again.
8. **Cross-machine state — passed (backend).** During the Omarchy backup the
   Windows snapshot carried the running Omarchy operation; Windows requests were
   refused with "Another operation is still running", also with Windows selected.
9. **Windows view — passed.** The Windows page still lists Open Aside (disabled
   while stopped) and the Windows wording; admin-backed and catalogue controls
   were not opened live.
10. **Windows anchor fix — not run.** Needs a host restart with Windows stopped. On
    this boot the anchor is mounted, so a Windows backup would take the normal path.
    Confirmed that the user can see the anchor's parent and its mount state without
    privileges, which the detection relies on.
11. **Backend cost — passed.** One daemon for all monitors: about 15 MB RSS and
    0.6% of one core including its probes (60-second sample, Omarchy VM selected).

Related fix outside this repository: `omarchy-vm view` used the legacy
`hyprctl dispatch focuswindow`, which Hyprland's Lua configuration rejects. It now
uses `hl.dsp.focus`, and focusing an existing viewer was verified.

## Known limitations

- One operation runs at a time across both machines (shared reservation and lock).
- A machine selected with `omachinesctl select` reaches an open panel at its next
  poll (up to 15 s); selections made in the panel are immediate.
- Omarchy backup labels live in Omachines' private state, not in the backup
  manifest, so `omarchy-vm backups` still shows the original label.
