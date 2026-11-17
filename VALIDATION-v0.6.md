# Omachines 0.6 agent workflow

Tested on 17 November 2026 with two existing guests on an Omarchy host. Both guests began stopped. The panel had the Omarchy guest selected.

## Client discovery and the host plugin

- Installed with `python3 install.py --agent-tools`. The live shell answered its IPC ping and listed the plugin as enabled. One backend from the installed 0.6 release served the three-monitor desktop.
- Requested the skill inventory from a fresh local Codex app-server and initialized a local Claude Code client. Both reported `omachines-vm`. Their links resolve to the same installed skill. The Codex VM MCP entries were disabled throughout.
- Started Windows for desktop work from the CLI with Omarchy still selected in the bar. The command waited for SSH, the signed-in Windows session and Cua. A later status check confirmed Windows had shut down.
- The skill passed its frontmatter validator. All 251 Python tests passed in private namespaces. The plugin manifest, qmllint and 39 offscreen Qt interaction tests passed.

## Omarchy guest

- The normal guest update created root Snapper snapshot 6. The Omarchy package was already 4.5.0-3. Cua Driver advanced from 0.33.4 to 0.47.5, gliff from 0.4.2 to 0.4.3, and mise updated its installed development tools. The updater exited successfully. A fresh `checkupdates` found no pending repository updates.
- A command over SSH returned the guest hostname and preserved exit code 37. The desktop probe recognized a live compositor with the SSH session variable unset. A deliberately stale value in just the probe environment produced an unavailable result.
- An independent agent used the installed skill to open a disposable guest window, read a button token and click it from a second CLI process in the same Cua session. The changed label appeared in the accessibility result. The host decoded a 360 × 190 PNG from the shared folder and read the changed label with OCR. The agent closed its fixture and ended its session.
- After a clean shutdown, the installed `start --desktop` reached authenticated SSH, Hyprland and Cua in 12 seconds on a cold boot. The guest's graphical session was active, Hyprland reported no config errors and the CLI connected to Cua on demand again.

## Windows guest

- Headless boot reached authenticated PowerShell, the interactive desktop and Cua Driver 0.47.13 through its existing named pipe. The command wrapper returned an explicit PowerShell exit code 37.
- Windows read a host-created marker in its shared folder and wrote a reply that the host verified. A Cua capture into that folder produced a valid 480 × 270 PNG on the host.
- The Windows checks covered agent connectivity and file transfer. They did not exercise Windows Update, RDP, backup or restore.
