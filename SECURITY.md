# Security

Omarchy plugins run with the desktop user's privileges. This plugin is not a
sandbox or a boundary between agents and personal Windows accounts. Existing
SSH/Cua/Aside bridges may have broad access to the guest and its signed-in apps.

Omachines adds no root daemon, network listener or credential store. Passive
checks do not request elevation. Explicit VM lifecycle and backup operations
use a visible terminal and the existing authorization boundary. Confirmations
reduce accidental destructive actions; they do not defend against a malicious
process already running as the same user.

Trust boundaries:

- Host wrapper executables, plugin settings and the optional backup workspace
  must be controlled by the user and reviewed before use.
- Backup source markers are compatibility checks, not script authentication.
- Guest responses are reduced to known status fields. The panel does not read
  browser pages, bookmarks or credentials for health checks.
- Reports and operation records are private local files. Reports omit account
  IDs, guest paths, window titles and raw command output. Review any report
  before attaching it to a public issue.
- Complete VM backups can contain credentials and signed-in browser state.
  Keep them encrypted and outside this repository. Archive verification does
  not prove that restoration has been rehearsed.

## Reporting a vulnerability

Use [GitHub's private vulnerability report form](https://github.com/atlastimed/Omachines/security/advisories/new).
Do not put credentials, VM images, private configurations or sensitive
logs into public issues. The project is an early preview; response times are
best effort.

## Repository checks

CI runs Gitleaks against Git history with redacted findings, plus a small
repository privacy check for accidental personal home paths, email addresses
and private artifacts. Neither automated checks nor a manual review prove the
absence of all secrets or identifying information.
