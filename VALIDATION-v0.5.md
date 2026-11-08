# Omachines 0.5 validation

Recorded on 8 November 2026 on Omarchy 4.5.0 (Hyprland 0.56.2, three monitors) with
Windows 11 26H2 build 26300.9711 under dockur 6.06. Host and guest measurements are from
the real machine. `scripts/check.py` covers the logic: 233 Python tests in a private PID
and network namespace, manifest validation, qmllint and 39 native Qt interaction tests.

## Incident during this work

A test run on this machine killed the desktop session. A test mocked `subprocess.Popen`,
which is global, and after a refactor reached `probes.Process` with a `MagicMock` pid.
That converts to 1, and `os.killpg(1, sig)` is `kill(-1, sig)`: SIGTERM and SIGKILL to every
process of the user (journal: all at 12:55:40.325, then the user service manager
`code=killed, status=9/KILL`). A replay with stubbed signals in a PID namespace recorded
`killpg(1, SIGTERM)` then `killpg(1, SIGKILL)`, and nothing once fixed. The user had to
power the PC off, which cut Windows off during an update.

Fixes: `probes.signal_group` refuses anything but a real child's pid, `probes.Process`
refuses a fake pid, `check.py` seals the tests away from the session, and `tests/sealed.py`
refuses a direct run on the desktop. Sealing also showed that five tests had been passing only
because they reached the live Windows SSH port.

Windows after that power cut: event 41 as expected, NTFS healthy and not dirty,
`Repair-Volume -Scan`, `chkdsk /scan`, `DISM /ScanHealth` and `sfc /verifyonly` all clean.
The update had finished all six winget apps first, because the VM outlived the session.

## Physical checks that passed

| Check | Result |
| --- | --- |
| Boot state | `stopped`, then `starting` at +3 s with ports open and no SSH banner, then `running` at +13 s. The old TCP check said "reachable" immediately. |
| Start, stop | Background, no password or terminal, through the polkit rule. Stop took 10 s. |
| Open while running | Viewer in 0.6 s, no prompt, password not on the command line. Class still `xfreerdp`. |
| Viewer minimized to the dock | Open brought it back to the active workspace and focused it. |
| Close viewer | Desktop returned to the console for agents. |
| Restart | 21 s without a viewer. With one open, it reopened 17 s after the request. Cua read `error` on a check right at the end of the restart and `ready` on the next one, moments later. |
| Update action | Copied the script and showed each stage. The first run updated six winget apps but lost power in its cleanup stage. Later runs had nothing left to do and finished with correct zero counts, so no run has yet both changed things and reported them. Afterwards the elevated task is gone. |
| Aside | Open Aside works on Cua 0.43.2; browser tools respond; CLI signed in; components at 1.26.1107.24. |
| Host shutdown wait | `systemctl stop docker` with Windows running: Windows logged 1074, 6006 and 109, no event 41 or 6008 on the next boot, not dirty. Took 4 s idle; one earlier stop took 40 s. |
| Panel | Opened from the real bar: Windows logo, machine switcher, version and update rows, new actions. |

## Independent review

A fresh reviewer read the change without running anything. Fixed from its findings:

- An update could hold the panel's lock for up to 4 hours after a guest crash. It now
  notices a dead task within two polls, and a guest silent for 10 minutes.
- "Windows restarted" could follow one missed probe. It now needs a new start time.
- A failed update could report success. It now ends as `partial`, worded as problems, and
  a program that cannot start counts as failed.
- `C:\\OEM`, including scripts run elevated, was writable by any signed-in user, which also
  applied to the elevated update task. The folder is now locked and the task removed after
  each run. Part of this predates version 0.5.
- The tests kept the network and part of the session's environment, and could be started
  directly. They now have no network, a fixed environment and a refusal on the desktop.

It found no way to widen the polkit rule.

## Measurements behind decisions

- **Idle CPU:** QEMU used 120-220% of a core just after a boot following 11 days off
  (Windows Update, Defender, TRIM). Settled: 9-11%, or 4% with a viewer. No KVM tuning was
  needed, so none was done. Guest counters under-report: Windows said 2% while QEMU used 90%.
- **vCPUs:** a moving full-window page used 44-122% of a core out of 400%. No evidence for
  raising 4 vCPUs for desktop use. Builds could still benefit; it is a panel setting.
- **RDP codec:** `/gfx:AVC444` first looked 50% costlier than `/gfx:progressive`, but
  alternating the order gave 49-74% against 44-48%, with 0 of 117,800 text pixels differing.
  Not a real result, so Omarchy's flag stays.
- **Ballooning:** at a 30% test threshold QEMU went from 8.0 to 4.0 GiB in 24 s and host RAM
  use from 56% to 43%. Windows stayed healthy and activated, with no new device problems.
  Set to 80% with a 4 GiB floor: QEMU stays at 8.0 GiB while the PC has room.
- **Agent screen:** 1920x1080 instead of 1280x800, set live, and it survived a restart.

## Not verified

- A real PC shutdown with Windows running. Stopping Docker exercises the same stop, but the
  ordering among all services only happens in a real shutdown.
- Ballooning under real memory pressure, only with the lowered threshold.
- Update failures other than the interrupted run; `windowsFailed` and `appsFailed` have
  fixture coverage only.
- The Omarchy VM was not started in this pass; its provider has fixture coverage only for
  the shared focus change.
