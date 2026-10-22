# Contributing

Keep changes small and describe their user-visible effect. Version 0.2 is an
early preview for one Windows integration; discuss new backends or privileged
operations before implementing them.

Run on Omarchy:

```bash
python3 scripts/check.py
python3 scripts/privacy_check.py
mise exec aqua:gitleaks/gitleaks@8.30.1 -- gitleaks git --redact --log-opts=--all
```

Gitleaks is optional development tooling managed through mise; it is not a
runtime dependency and does not require a global tool selection. CI uses the
same pinned upstream version with an archive SHA-256 check.

The Qt tests use installed Omarchy components in an isolated offscreen
Quickshell instance. Keep physical Hyprland verification distinct from those
tests. Ask the machine owner before stopping Windows or closing their apps.

Never commit host/guest credentials, real usernames in test fixtures, local
runtime state, screenshots of personal apps, VM images or backups. Use generic
examples and your GitHub noreply commit address. Scanner output must be redacted.

The installer only packages an explicit runtime allowlist. Preserve that
boundary and the user's other shell settings. Changes to backup/restore logic
need focused failure and concurrency tests, not just a successful happy path.

Administrator-helper changes must be installed explicitly and tested separately
from the user plugin. Use disposable storage fixtures for production swap failures;
never use a contributor's real VM as a destructive restore test. Live rehearsal
must disable all networking and host shares. Keep backup integrity evidence and
reviewed boot evidence separate. Include a parameter-preservation regression when
changing privileged configuration, and retain prior configuration and storage.
