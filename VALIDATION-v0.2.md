# Version 0.2 validation — 22 October 2026

## Automated checks

On Omarchy 4.0.6, `python3 scripts/check.py` passed with 87 Python tests, manifest
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

Final resource/recovery checks and publication scan results will be recorded below
before the version is marked ready.
