# Omachines development

Read README.md and PLAN.md. This is a native Omarchy plugin for existing VMs
(Omarchy Windows and the `omarchy-vm` Omarchy VM), not a VM installer. Use the installed Omarchy skill for integration work.

- Keep changes in this repository and the user-owned plugin install. Never edit
  `/usr/share/omarchy` or duplicate the system virtualization stack.
- Preserve the existing Windows wrappers, encrypted disks, backup files and
  root authorization boundary. Never store credentials or guest content here.
- Ask before tests that stop/restart either VM or close user applications.
  Read-only probes of a running VM (`omarchy-vm status --json`) are fine.
- Use one shared service across monitors, theme-native controls and bounded
  probes. Readiness is not agent activity. State may be stale or unknown.
- Run `python3 scripts/check.py` on Omarchy. It includes actual Qt interaction
  tests using an isolated offscreen Quickshell runtime. Record separate physical
  desktop evidence. No extra long-running shell instance or web UI.
- `python3 install.py` stages an explicit release; content-addressed entry paths
  avoid stale QML caches. Preserve settings and data on update/removal.
- Keep publication local unless the user explicitly authorizes publishing.
