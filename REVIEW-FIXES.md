# PR #1 review fixes

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

## Self-review and verification

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

## Verification limits

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
