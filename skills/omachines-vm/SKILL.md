---
name: omachines-vm
description: Use the existing Omachines Omarchy Linux or Windows VM from the host for guest commands, desktop automation, testing software, transferring artifacts, or checking guest readiness. Use when a task names either VM or asks for work inside an Omachines guest.
---

# Work in an Omachines guest

Run `omachines-agent` on the **host**. Always name `omarchy` or `windows`. For a request to test in the Omarchy VM, the compositor, applications, tool paths and screenshots all belong to that guest.

## Connect

```bash
omachines-agent omarchy status
omachines-agent omarchy start           # Headless. Returns when a real guest command succeeds.
omachines-agent omarchy start --desktop # Also waits for the signed-in compositor and Cua.
omachines-agent omarchy run 'hostname; id -un; pwd'
```

Use the same commands with `windows` for Windows. `status` is a fresh observation and can succeed while the VM is stopped. `start` is safe to repeat after boot. Start only for work that needs a running guest. Use `stop` after a temporary run if you have authorization to shut it down. Preserve a guest already running for someone else.

## Commands and files

- Pass one single-quoted guest command to `run`. Omarchy uses Bash. Windows uses PowerShell, e.g. `omachines-agent windows run '$env:COMPUTERNAME'`. The wrapper forwards stdin, stdout, stderr and the remote exit code. In PowerShell, use `exit $LASTEXITCODE` to propagate a native program's failure.
- For multiline Bash, feed a quoted heredoc to `omachines-agent omarchy run 'bash -se'`. Guest SSH inherits the signed-in desktop environment on this setup. For long-running work, start a guest user service with `systemd-run --user` and check its status in a later call.
- Build in the guest filesystem. Transfer a checkout with `rsync -a ./ omarchy-vm:project/` from its host directory, excluding secrets and host build artifacts as appropriate. Windows has `C:\Work`. Host `~/OmarchyVM` maps to guest `~/Shared`. Host `~/Windows` maps to guest `\\host.lan\Data`. Use a fresh task directory for shared artifacts.
- The existing pinned SSH aliases also support `scp` and SSH port forwards. For the Omarchy login or update that needs a terminal, use `ssh -t omarchy-vm`. Passwordless sudo is scoped to the Linux guest. Use `omarchy update -y` there for an authorized update.

## Guest desktop

An existing chat can use the guest even with its VM MCP server disabled. Use the guest Cua bridge supplied by the client if connected, or the following CLI from the host. The host's local Cua tools target the host desktop.

```bash
omachines-agent omarchy cua list_windows '{"app":"foot","session":"my-vm-task"}'
omachines-agent omarchy run 'cua-driver describe get_window_state'
```

Use a distinct session label for the task. Repeat it on every tool accepting `session`. Inspect the intended guest window using the returned PID and window ID, then act on a current element token and verify the result. Read the installed cua-driver skill or use the guest tool's `get_guide` for complex interactions. A persistent MCP connection supports workflows needing one transport across calls. End only your own named session with `omachines-agent omarchy cua end_session '{"session":"my-vm-task"}'`.

For a Linux full-screen PNG, `omarchy-vm screenshot "$host_output"` takes a **host** output path. The CLI's `cua … --image` takes a **guest** output path. Use an absolute path under the shared task directory to read the file back on the host. On Windows, `--image` can also use a Windows-local path followed by `scp`.

## If a connection fails

- Check `status` and the panel's **Agents** tab. SSH, the signed-in desktop and Cua are separate checks. An operation or outside maintenance can temporarily take the guest offline.
- On Omarchy, `omarchy-vm status --json` shows QEMU state. `omarchy-vm view --spice` can show a guest still booting. Inspect a changed SSH host key at the guest console before changing its pin.
- On Windows, SSH can work while the interactive session is locked or disconnected. Use the panel's console handoff for an authorized disconnected session or ask the user to unlock it. The Aside browser bridge is separate.
- Backups, restores and resource changes have their own controls and confirmations in Omachines. Use the existing lifecycle and authorization paths.
