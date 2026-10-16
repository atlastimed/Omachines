# Version 0.1 validation — 16 October 2026

## Automated checks

`python3 scripts/check.py` passed on Omarchy 4.0.4:

- 27 Python tests cover typed/unknown/stale state, operation reservations and locks,
  redacted diagnostics, backup evidence, installer preservation, release staging,
  and publication privacy heuristics.
- 8 native Qt interaction cases inside the actual Quickshell runtime: navigation,
  explicit confirmation/cancel, busy/demo states, optional Aside, keyboard settings,
  and reactive light/dark theme tokens. The harness reports zero failures.
- Omarchy manifest validation and qmllint with zero warnings.

GitHub Actions runs portable backend tests on Python 3.12 and 3.14, publication
privacy heuristics, and a redacted Gitleaks scan of Git history. See the workflow
run attached to each commit for its result. Native Omarchy checks remain a
separate local requirement.

## Publication checks

Before the first public push, Gitleaks 8.30.1 scanned the exact staged tree and
both initial commits with no findings. Privacy heuristics checked all 27 tracked
files, and manual review removed a personal fixture name and private backup
details. Commit identities use the public project alias and GitHub noreply email.
No VM images, credentials, private configuration or desktop screenshots are
included. These checks reduce accidental disclosure; they are not a guarantee.

The initial [GitHub Actions run](https://github.com/atlastimed/Omachines/actions/runs/38959128102)
passed Python 3.12 and 3.14 checks and the Git-history secret scan. The full
implementation is in [draft PR #1](https://github.com/atlastimed/Omachines/pull/1),
pending review before merging. GitHub secret scanning, push protection and
private vulnerability reporting are enabled.

## Physical desktop

- Real Hyprland panel observed and captured on the 1920×1080 display.
- All three monitors expose one VM widget; exactly one Python daemon is shared.
- Overview, Backups and Settings were visually inspected; right-arrow/number
  navigation, notification-toggle persistence, and Escape close were exercised.
- User confirmed the current panel size is comfortable.
- Theme colours/fonts/controls are the installed Omarchy components. Light/dark
  token changes were tested offscreen; the user's live theme was not switched.
- Installer update, uninstall and reinstall passed. Unrelated shell.json content
  compared equal to the pre-install backup. No Hyprland configuration errors.
- Current plugin loads without plugin-related QML errors. Existing first-party
  duplicate-handler warnings were not caused or modified by this plugin.
- Idle sample: one backend, 20,176 KiB RSS; 0.03 CPU seconds over 79.3 seconds
  (~0.038% of one core). This measures the Python daemon, not all shell/guest work.

## Real Windows integration

The user authorized the disruptive test. A new full cold backup and the final
Windows recovery check completed successfully. The initial baseline and
original development-ready backup were retained.
Archive listing and SHA-256 verification passed. The provider held Omarchy's
root-owned lifecycle lock throughout the disk copy and verification. The plugin
reported durable progress and disabled conflicting actions. A plugin reload
request during verification did not stop the independent backup worker.

Windows automatically signed in after restart. SSH, interactive desktop and Cua
were healthy. The viewer reopened. Aside launched through the plugin, and a later
read-only MCP connection check succeeded. No page titles, bookmarks, account
contents or credentials were collected by these health checks.

The existing Windows setup backup script was updated to emit progress stages,
use Omarchy's lifecycle lock while copying, and record failure/recovery status.
No new root service, network listener, virtualization stack or credential store
was installed.

## Boundaries

Archive verification is not a restore rehearsal. Restore and CPU/RAM editing are
not implemented in v0.1. The current adapter uses the existing Windows wrappers
and local Omarchy port/share conventions; additional VMs/providers are future
work. Readiness does not indicate an agent's activity. Aside's browser connection
is checked, but its AI model authentication, subscription and usage are not.
