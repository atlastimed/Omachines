# Version 0.2 validation — 22 October 2026

## Automated checks

On Omarchy 4.0.6, `python3 scripts/check.py` passed with 89 Python tests, manifest
validation, zero-warning qmllint, and 17 native Qt interaction cases (18 passes
including initialization). Cases exercise actual key events in the installed
Quickshell runtime; this is separate from the physical desktop evidence below.

New coverage includes registry migration and malformed/unknown-provider rejection,
immutable operation parameters, bounded private history, archive traversal/link/
duplicate rejection, missing TPM state, both supported TPM layouts, a real GNU tar
sparse-disk round trip, allocation capacity and configuration preservation,
failed-start recovery, typed restore cancellation, retained storage, failed mount,
and interrupted rollback recovery. Filesystem swap tests use temporary fixtures;
they never replace the real Windows storage.

Native tests cover backup-name and numeric editing without panel shortcut
interception, selection captured before confirmation, Cancel as the initial focus,
busy-state dismissal and the existing keyboard, theme and optional-integration
cases. The full check remains required locally; generic Linux CI cannot exercise
Omarchy's native components or prove the physical desktop.

## Physical desktop and guest

The updated panel was installed with the content-addressed installer and viewed
on the real Hyprland desktop. Backup naming, metadata and resource controls fit the
compact panel. A physical keyboard interaction entered 6 CPUs, opened the matching
confirmation and cancelled it without altering the VM. The user's theme was not
changed; light/dark token changes were checked in the isolated Qt harness.

The updated Aside CLI reports version 1.26.1022.0. The versioned updater junction
needed a constrained resolver for elevated SSH. The existing private guest/host
bridge was updated locally; these machine-specific wrappers are not published.
CLI installation and authentication are reported independently. When Aside is
closed, authentication remains unknown instead of misreporting a broken CLI.
No model-backed task, browser-page inspection or account migration was performed.

## Backup and recovery

The user authorized Windows restarts. The built-in provider created a new complete
cold backup and checked its archive structure and SHA-256. It retained previous
backups and restarted Windows. The backup was named through the normal action
worker, confirming label persistence and operation history.

The first test attempt rejected a valid single-file TPM layout. Its incomplete
archive was retained and Windows restarted. The validator was corrected and a
regression test added before the successful retry.

The new backup was freshly verified, extracted separately, and booted in a
container configured with no network, published ports or host shares. Its private
QMP screenshot showed a normal Windows desktop after automatic local sign-in.
This proves boot recovery for that copied disk/TPM/firmware combination; it does
not prove every application or a production storage swap. The original storage
was not replaced. Copies and incomplete work remain private and outside Git.

The original guest completed a real allocation round trip from 4 to 6 to 4 CPUs;
each value was queried inside Windows after restart. RAM remained 8 GiB. Docker
inspection confirmed the rehearsal container used network mode `none`, no port
bindings, only its copied `/storage` mount and no privileged mode. The reviewed
copy was stopped; the original guest remained available.

## Publication and self-review

The staged publication tree and Git history passed Gitleaks 8.30.1 with redacted
output. Privacy heuristics passed for all tracked files; private review notes,
VM images, credentials, operational evidence and real desktop screenshots remain
outside publication. Manual review tightened directory pinning, restore/rollback
journals, immutable request parameters, stale readiness and editor draft handling.

Initial CI exposed a new Aside probe escaping a mocked response fixture. It was
isolated, external commands are now forbidden in that fixture, and missing-bridge
and disconnected-state regressions were added. Current CI results are attached to
PR #2; the initial failing run is not treated as successful validation.


## Final acceptance

All three physical monitors showed the updated native panel. The smaller 1080p
display scrolled the backup actions into view through keyboard navigation. The
normal Windows viewer opened and a second Open action focused the existing viewer.
The normal administrator action worker completed a catalogue refresh and recorded
its completion in history. No Hyprland configuration errors were reported.

After the final restarts, Windows reported 4 CPUs and approximately 7.9 GiB usable
RAM (8 GiB configured). SSH, interactive desktop, Cua and Aside browser tools were
ready; the CLI version check was ready and its account was signed in. Model access
and usage were deliberately not exercised.

Exactly one backend served all monitors. A 61-second sample measured 0.03 CPU
seconds (about 0.049% of one core) and 21,204 KiB RSS. This measures the Python
backend, not the full shell, transient probes, Windows or the hypervisor.

The final GitHub checks must be read at the PR's current head: Python 3.12,
Python 3.14 and the Git-history secret scan. The initial failed test-isolation run
was superseded by passing checks. All publication files passed the privacy scan.
The real baseline backups, the newly verified backup, the stopped rehearsal copy
and the failed attempt's incomplete archive were retained; none are published.
