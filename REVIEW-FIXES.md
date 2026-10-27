# Review fixes

## 27 October 2026 second pass (v0.3.1)

A fresh five-reviewer pass over `cf7da31` reported four P2 findings, each reproduced
in an isolated fixture. R1 is in this repository; R2–R4 are in the Omarchy VM tooling
(`omarchy-vm`), which now has its own tests beside it.

| Finding | Change | Regression evidence |
| --- | --- | --- |
| R1: Windows **Open desktop** completed before its startup | The launcher runs under `supervise.py --until-released`, which holds the operation lock until the worker sees the viewer window and releases it; the viewer then runs on its own. A launcher that exits first fails the operation ("authorization was cancelled"); one still waiting after 10 minutes is stopped with its process group and the request is withdrawn. A killed worker leaves the lock with the supervisor until the launcher ends, as for other providers. | Real worker and supervisor with a fake launcher: while authorization is pending, operations and installer maintenance are refused; after the viewer appears the lock is free and the viewer keeps running; cancellation fails cleanly; an unanswered prompt approved after the timeout never starts Windows. Release mode leaves its program running and otherwise reports its exit status. All fail on `cf7da31`. |
| R2: RAM change stopped the VM before checking it could restart | `omarchy-vm resources` refuses, before saving or stopping anything, a size larger than the memory free plus what the running VM gives back. If the restart still fails, the previous allocation is restored and started, and the command fails saying so. | Growth that cannot fit, growth that fits, reductions, stopped VMs and a restart failure after the check; and live: a 24 GiB request with 12.7 GiB obtainable was refused and the VM kept running. |
| R3: two plain `create` commands could overwrite a disk | The disk's existence and a stopped VM are checked only while holding the lifecycle lock. | The review's interleaving with real `flock` and `qemu-img`: the delayed create refuses and the disk is unchanged; a running VM is never given a new disk. |
| R4: CPU writer and reader disagreed above 16 | One rule (`MAX_CPUS`) checks the whole configuration when it is read and before a change is saved; changes accept at most `min(16, host CPUs)`. | A 24-CPU host: 17 refused before anything is written or stopped, 16 accepted, the saved file always readable. |

While adding those tests, one regression from the first system-review commit was
found and fixed: `omarchy-vm start` had lost its wait for QEMU's monitor (it moved
into the post-maintenance restart helper), so `start` returned before QEMU answered.

### Verification

`python3 scripts/check.py` passes 192 Python tests, manifest validation, qmllint
without warnings and 35 native Qt passes; the privacy check is clean. The Omarchy VM
tooling's 12 tests pass (`python3 -m unittest discover -s tests` in its folder).

### Limits

R1 was tested with a fake launcher; a real Windows authorization prompt was not
left pending. The R2 restart-failure rollback was tested with fakes only.

## 27 October 2026 system review (v0.3.1)

A second three-reviewer pass covered the whole system: this plugin, the Omarchy VM
tooling (`omarchy-vm`, kept outside this repository), the Windows VM and how they
connect. Findings outside this repository were fixed in the Omarchy VM tooling and
documented in its README; this section lists what changed here and what this
repository relies on.

| Area | Change | Regression evidence |
| --- | --- | --- |
| Omarchy VM lifecycle outside the panel | `omarchy-vm` now holds a lifecycle lock for backup, restore, resources, create and stop, and reports `unitState` and `maintenance` in `status --json`. The provider reads both through allowlists: a lifecycle command no Omachines operation started shows as "<name> in progress outside the panel", disables the same controls as an Omachines operation, skips guest probes and is followed by an immediate deep check. `deactivating` shows as shutting down, `failed` as stopped unexpectedly, `activating` as starting. | Unit-state and maintenance validation and classification, outside maintenance named unless it is ours, checks skipped and due again afterwards, daemon recheck, and real key presses and clicks on the panel while it lasts. |
| Results the panel could misreport | A stop that ended by forcing QEMU off, a backup forced off first, a backup saved whose restart failed, and a restore swapped whose restart failed are each reported as such, instead of as success or as a failed backup or restore. | Fake `omarchy-vm` output for each case, including reasons that are hidden because they contain paths. |
| Terminal closed during an Omarchy VM restore | Omarchy VM commands run detached in their own session, so the operation terminal's hangup, termination and Ctrl+C reach only the worker, which waits and records the result. Windows commands stay attached because sudo needs the terminal. | Real worker processes: the provider's session differs from the worker's and is led by the supervisor; only detached providers get one. |
| Registry lock-out | Fixed fields and connections always come from the code. A stored registry is rewritten atomically on load, keeping the selection and a valid name; a migration never overwrites a file changed meanwhile. Previously any change to the fixed fields, such as the Omarchy VM leaving TCP SSH, made both machines unusable. | Stored fixed fields replaced and selection kept, tampered connections never run, invalid names and list-valued providers fail safe, concurrent change preserved. |
| Starting Windows with too little memory | Start, open and the backup path that starts Windows refuse while less memory is free than its RAM allocation; systemd-oomd could otherwise kill a VM outright. | Short and sufficient memory, unknown allocation, already running. |
| Root helper staging | Restore takes the lifecycle lock before extracting; a failed extraction or shutdown removes its staging copy. Backup creates its `.incomplete` folder only once both locks are held. The rehearsal screen capture is copied only as a plain file of bounded size, never through a link. | Real tar failure on a junk archive, a held flock, failed shutdown, ordering, refused backup, symlink, FIFO and oversized captures. |
| Installer and wording | The installer says when the installed helper is older than the release. The empty-backup hint points below, where its action is. | Version comparison and silence when no helper; hint position checked against the rows and clicked. |

### Verification

`python3 scripts/check.py` passes 187 Python tests, manifest validation, qmllint
without warnings and 35 native Qt passes.

Live, against the real Omarchy VM after it moved to passt networking and SSH over
vsock (throwaway Omachines state where the plugin's code ran): the deep check from
this branch reported SSH, desktop and Cua ready; holding `omarchy-vm`'s lifecycle
lock made the panel's state read "Backup in progress outside the panel" with
checks paused; and `omarchy-vm` itself refused start and view during it, stopped
cleanly over SSH and over the ACPI button, made a backup (`cleanShutdown` true,
71 s) and restored it through the journaled swap (66 s, the guest's newer file
gone, the previous disk kept, normal boot).

### Limits

Windows paths were again exercised with fakes only. The forced-stop messages were
tested with fake output; the real VM was not forced off. Helper 0.3.1 must be
reinstalled for the helper changes, and the installed plugin updated, before the
installed panel reads the new status fields.

## 27 October 2026 review (v0.3.1)

An independent three-reviewer pass over `c6071e1` reported one P1 and seven P2
findings. Each was reproduced with the review's scripts before fixing, and each fix
has a regression test that fails on `c6071e1`.

| Finding | Change | Regression evidence |
| --- | --- | --- |
| R1 (P1): provider outlives the lock | Every VM command a worker runs (Omarchy VM commands, the Windows helper, legacy backup script and `__priv up/down`) runs under `backend/supervise.py`, which inherits the locked `operation.lock` and lives exactly as long as the command. It relays output while the worker reads it and drains it afterwards, so the command never meets a broken pipe. A worker sent TERM, HUP or INT waits for its command, records the real result, then exits; a closed operation terminal no longer turns into a false failure. A killed worker leaves the operation shown as still running until the command ends, then interrupted. | Real worker processes with a fake `omarchy-vm` during backup, allocation and restore: after SIGTERM the worker stays, a second request and installer maintenance are refused, and the outcome is recorded; after SIGKILL both stay refused until the command ends, its later output does not break it, and the state becomes interrupted. Supervisor exit status and terminal pass-through. |
| R2: cancelled restore leaves a copy | Helper 0.3.1 checks space, then asks for the typed confirmation, and only then extracts. The extracted copy must contain Windows storage before anything stops. End of input cancels. | Cancel and EOF never extract or stop Windows; insufficient space refuses before asking; order is confirm, extract, stop; extraction without storage is refused. |
| R3: Tab into a disabled form | Forms focus only a visible, enabled editor and report whether they did; otherwise the panel keys keep focus. Focus that falls to the window root while the panel is open (a focused control disabled when an operation starts) returns to the panel keys. | Real key events: empty backups and busy forms keep arrows, 1–4, R and Escape; enabled forms still take focus; an operation started from a focused form returns focus. |
| R4: shown and submitted allocation differ | The fields, confirmation and request all use the draft clamped to the provider's limits, which match the backend ranges. An allocation outside them is noted; the confirmation names the current allocation. | Below-minimum and above-maximum allocations and changed limits agree across field, confirmation and payload. |
| R5: reinstall loses options | Uninstall saves the plugin's own options and bar position to `installer.json`; reinstall restores them one field at a time through the shell, never over newer values, and validates them against the manifest schema. | Every option round-trips through uninstall/reinstall while another widget changes; saved state is validated; an explicit workspace wins. |
| R6: failed first activation reported as success | The intent to activate is saved before publishing and cleared only once the shell confirms the bar entry. A retry completes it; an ordinary update leaves a deliberately removed entry off. Failures say to run the installer again. | Failure before mutation then retry gives exactly one entry; lost acknowledgement keeps newer options; an update of a removed widget changes nothing. |
| R7: Windows readiness after SSH loss | When only the console port answers, SSH is offline and the desktop, Cua and Aside checks are unknown, deep check or not. When SSH answers again, checks gathered without it become "due" and the service runs a deep check at once. The Omarchy provider uses the same rule. | Deep-ready → SSH lost → SSH restored with shallow polls, including the immediate recheck rule. |
| R8: hover and Enter target different settings | Hovering a toggle or an interval choice moves the keyboard cursor to it, as on the action rows and native panels; Enter selects the hovered interval. | Mixed hover and keyboard across Aside, notifications and an interval choice. |

The UI fix also covers two cases the review did not list: a focused form control
disabled by a starting operation left focus on the window root, and focusing a
disabled field left a stale focus flag that stole focus when it was enabled.

### Verification

`python3 scripts/check.py` passes 155 Python tests, manifest validation, qmllint
without warnings and 33 native Qt passes. R1 was also checked live against the real
Omarchy VM with throwaway Omachines state, using its read-only backup integrity
check: after SIGKILL of the worker, maintenance stayed refused and the state read
"still running" until the check finished 14 s later, then "interrupted"; after
SIGTERM the worker waited and recorded "Backup checksum and contents verified".

### Limits

Windows paths were exercised with fakes only: no live Windows restore, backup or
SSH loss. Qt results are offscreen; physical keyboard and pointer use on the
installed panel is not yet checked. A supervisor that is itself killed with
SIGKILL releases the lock early, as the worker did before; it does no other work,
so that needs a deliberate kill. Helper 0.3.1 must be reinstalled for R2.

## PR #1 review

The single independent review covered `badcd8a`. All seven findings were
accepted after checking the implementation and installed Omarchy contracts.
The fixes remain within the original Windows-adapter scope.

| Finding | Change | Regression evidence |
| --- | --- | --- |
| R1 — obsolete workers | One-use request IDs, startup expiry and atomic consumption under a shared admission gate; demo is checked again at execution. Running state also requires a held worker lock. | Expired/superseded/wrong-action requests; duplicate consumption; demo toggled before execution; two delayed launchers; a worker starting before its parent releases admission; obsolete PID records. |
| R2 — output buffering | Incremental binary stdout reads stop at 2 MiB plus one detection byte; timeout covers reading and process exit. | Finite 8 MiB producer is killed before its completion marker, without requiring newlines; normal output, nonzero exit, timeout and EOF-before-exit. |
| R3 — orphan probes | Shared probe-group ownership, bounded TERM/KILL cleanup even after the leader exits, and cleanup on owner shutdown/daemon EOF. Lifecycle workers remain separate. | TERM-resistant descendant, repeated cleanup, normal and threaded owner termination, signal delivery, and detached lifecycle worker survival. |
| R4 — malformed status | Typed MCP envelopes/results and optional guest fields; malformed Cua data affects its own check; unexpected failures invalidate readiness and remain visible through shallow polls. | Null/scalar/list responses and fields, valid SSH alongside malformed Cua, ready → failed deep probe → shallow poll → successful recovery. |
| R5 — maintenance race | One lock order across admission, worker consumption and maintenance. Maintenance retains exclusion through validation, file changes, rollback and native settings operations. | Pending/active worker rejection, requests and another installer during a transaction, rejection during maintenance, and release after rollback. |
| R6 — lost settings | Updates no longer disable the plugin or restore an old settings snapshot. File rollback changes only files; explicit settings operations use Omarchy's current-state API. | Concurrent unrelated and Omachines edits during success/failure, initial placement, one-field workspace update, removal, malformed/symlinked configuration and a lost settings acknowledgement. |
| R7 — keyboard focus | Confirmation dismissal releases button focus and restores the panel key catcher; hidden buttons cannot activate. Busy/demo dismissal and queued callbacks are guarded. | Actual native Return/arrow/Escape events after Cancel, Continue, automatic dismissal and queued close; initial Cancel focus; no unintended action or panel close. |

Additional mocked-provider tests cover failed and interrupted backup creation,
unchanged completion records, history preservation and successful connection
recovery. A new backup must advance its absolute completion path; this does not
change the trust placed in the reviewed external provider's verification.

### Self-review and verification

The implementation was checked again after the fixes, including lock ordering,
spawn/registration windows, process-group ownership, failure-state retention,
native settings APIs and actual keyboard-event routing. That pass added coverage
for early worker startup, signal inheritance, threaded shutdown, stale live PID
records and settings acknowledgement failure. No second independent review was
requested.

`python3 scripts/check.py` passes 63 Python tests, manifest validation, qmllint
without warnings, and 13 native Qt interaction tests. GitHub's Python and secret
scanning checks run on the final pushed commit; consult the PR checks for their
current result. Privacy and secret scans include the new tracked files.

### Verification limits

All new process/installer/backup tests use disposable fixtures or mocked
providers. Native UI verification is offscreen in the installed Quickshell
runtime. This round did not update the live plugin, reload the user's shell,
stop Windows, run a real backup, or rehearse restoration. The earlier physical
desktop and successful cold-backup evidence is recorded separately.

The revised installer follows the installed native `rescanPlugins`,
`putBarWidget`, `setBarWidget` and `setPluginEnabled` contracts. The current
checkout is ready for a separately coordinated live update; that update and
physical post-update checks have not been performed in this fix round.

Process-group cleanup covers normal exit and catchable shutdown signals. It
cannot guarantee cleanup after owner SIGKILL or contain host programs that
deliberately leave their process group. Real external-provider recovery after
terminal loss or host failure remains untested; archive verification still is
not a restore rehearsal.
