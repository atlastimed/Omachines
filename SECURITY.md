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
- Probe stdout is capped while reading, including data without newlines. Probe
  process groups are cleaned up on completion, timeout, normal owner exit,
  SIGTERM and SIGHUP. Detached lifecycle workers have a separate lifetime.
  This is not containment for a malicious host executable that escapes its
  process group, nor a cleanup guarantee after an uncatchable owner SIGKILL.
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

## Version 0.2 recovery boundary

The optional administrator helper is installed separately, root-owned, and invoked
with Python isolated mode. Its runtime does not import project code. It uses fixed
system executable paths, filtered command environments and an action allowlist.
It verifies root-owned ancestors, protected storage identity and authenticated user
identity. No sudoers or polkit rules are added. Helper installation itself is an
explicit code-trust decision; a compromised checkout must not be installed as root.

Backups in a user-writable parent are accessed through pinned directory/file
descriptors. New writes remain in a pinned private staging directory. Verification
requires a protected root-owned archive and checksum, hashes the pinned file, and
rejects traversal, links, devices, duplicate entries and incomplete disk/firmware/
TPM state. Sparse extents drive capacity estimates. Extraction uses a separate
root-protected staging directory; existing storage is retained during replacement.
The journal supports retrying an interrupted rollback. Production filesystem swaps
have fixture coverage; a disposable boot is distinct from testing replacement of
the user's production storage.

Rehearsal uses the locally installed image with no network, published ports or host
shares. It exposes only KVM and the copied storage to the container. A private QMP
screenshot supports explicit boot review; there is no host-accessible QMP listener.
The clone still contains credentials from the backup and is retained privately.
This is isolation for a restore test, not a sandbox guarantee against a hostile
kernel/hypervisor exploit. Guest code is never trusted as a host command source.
