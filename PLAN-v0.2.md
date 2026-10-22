# Omachines 0.2

Agreed scope: named backup management, integrity rechecks and guided restoration;
CPU/RAM controls; actionable connection diagnostics and Aside CLI/authentication
status; a bounded operation history; per-machine configuration and provider
boundaries for a later Linux VM. No VM creation, disk resizing, automatic deletion,
agent task launch or invented activity indicators.

The native panel remains theme-driven, compact and shared across monitors.
The existing Windows guest, credentials, signed-in browser and backups are preserved.

## Implementation and acceptance

- [x] Versioned machine registry; migrate the existing Windows identity without
      changing connections. Unknown providers fail closed.
- [x] Private bounded operation history with immutable request parameters.
- [x] Self-contained interactive administrator helper for the existing Omarchy
      Windows provider. Root-owned installation, fixed command allowlist, no new
      passwordless privileges or background root service.
- [x] Backup catalogue, labels, size/age, fresh integrity verification and distinct
      restore-test evidence. Preserve v0.1 records and imported backups.
- [x] Recovery plan plus offline disposable restore rehearsal. Production restore
      retains the old storage, validates capacity and requires explicit selection
      and confirmation. Recovery remains possible after interrupted transactions.
- [x] CPU/RAM preflight, explicit apply/restart, preservation of other compose fields.
- [x] Separate Aside browser, CLI and authentication checks; useful connection
      instructions without secrets. Preserve the user's debugging/session settings.
- [x] Native forms, keyboard focus/confirmation, constrained sizing and theme checks.
- [x] Unit/integration tests for privilege boundaries, restore paths, interruption,
      stale requests and registry/history migration; complete existing checks.
- [ ] Live update, physical panel checks, authorized lifecycle/resource checks and
      offline restore rehearsal, followed by healthy original Windows guest.
- [ ] Self-review, secret/PII scan and GitHub PR with validation limits.

The user authorized Windows/Aside restarts during this work on 22 October 2026.
Restoring over the active VM is a distinct destructive operation and is not needed
for the initial rehearsal; a disposable restored copy will be used instead.
