"""Omarchy VM adapter: the user-level omarchy-vm QEMU guest. No root is used.

Only the fixed commands named in the machine registry are run. Their output is
reduced to known fields; raw guest output is never stored or reported.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from core import (OperationError, carried_checks, check, cua_check, fresh_checks, hypr_windows, machine_operation,
                  now, operation, read_json, run, state_dir, write_json)

BACKUP_ID = re.compile(r"omarchy-\d{8}T\d{6}Z")
ID = "omarchy"
# One read-only probe: CPU count, memory, root filesystem and whether the
# auto-signed-in Hyprland session has published its environment.
PROBE = ("printf 'cpu=%s\\n' \"$(nproc)\"; "
         "awk '/^MemTotal:/{print \"memoryKiB=\" $2}' /proc/meminfo; "
         "df -B1 --output=size,avail / | awk 'NR==2{print \"disk=\" $1; print \"free=\" $2}'; "
         "if systemctl --user show-environment 2>/dev/null | grep -q '^HYPRLAND_INSTANCE_SIGNATURE='; "
         "then echo desktop=available; else echo desktop=unavailable; fi")
STAGES = {"copying": "Copying the VM disk and firmware", "verifying": "Verifying the archive and checksum",
          "starting": "Starting Omarchy VM again", "restored": "Backup restored"}
SAVED = re.compile(r"Backup omarchy-\d{8}T\d{6}Z saved and verified")
UNIT_STATES = ("active", "activating", "deactivating", "reloading", "inactive", "failed")
# omarchy-vm commands that hold its lifecycle lock, as the panel names them.
MAINTENANCE = {"backup": "Backup", "restore": "Restore", "resources": "Resource change", "create": "VM creation",
               "stop": "Shutdown"}


def executable(path):
    path = Path(path)
    return str(path) if path.is_file() and os.access(path, os.X_OK) else None


def control(machine):
    command = executable(machine["connections"]["control"])
    if not command:
        raise OperationError("The omarchy-vm command is missing")
    return command


def number(value, low=0):
    return value if type(value) is int and value >= low else None


def stamp(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9T:.+\-Z]{10,40}", value) else ""


def known(value, names):
    return value if isinstance(value, str) and value in names else None


def label(value):
    return value.strip() if isinstance(value, str) and 1 <= len(value.strip()) <= 60 and not any(ord(c) < 32 for c in value) else ""


def parse_status(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("running"), bool):
        raise ValueError("Invalid Omarchy VM status")
    running = raw["running"]
    guest = raw.get("guest")
    return {"running": running,
            "guest": guest if running and isinstance(guest, str) and re.fullmatch(r"[a-z][a-z-]{0,29}", guest) else None,
            "sshReady": running and raw.get("sshReady") is True, "viewerOpen": raw.get("viewerOpen") is True,
            "installMedia": isinstance(raw.get("installMedia"), str) and raw["installMedia"] != "",
            "diskUsedBytes": number(raw.get("diskUsedBytes")),
            # Newer omarchy-vm: the systemd unit state and a lifecycle command
            # holding its lock, from any terminal or agent. Unknown means absent.
            "unitState": known(raw.get("unitState"), UNIT_STATES), "maintenance": known(raw.get("maintenance"), MAINTENANCE)}


def status(machine):
    return parse_status(json.loads(run([control(machine), "status", "--json"], 8)))


def outside(st, op):
    # omarchy-vm runs a lifecycle command this machine's Omachines operation
    # did not start; the panel treats it like its own operation.
    return bool(st.get("maintenance")) and machine_operation(op, ID).get("status") not in ("starting", "running")


def classify(st, op):
    state, text = lifecycle(st, machine_operation(op, ID))
    return state, (MAINTENANCE[st["maintenance"]] + " in progress outside the panel" if outside(st, op) else text)


def lifecycle(st, op):
    if not st["running"]:
        if op.get("status") in ("starting", "running") and op.get("action") in ("backup", "restore"):
            return "stopped", "Backup in progress" if op["action"] == "backup" else "Restore in progress"
        if st.get("unitState") == "failed":
            return "stopped", "Omarchy VM stopped unexpectedly; check journalctl --user -u omarchy-vm"
        return "stopped", "Omarchy VM is stopped"
    if st.get("unitState") == "deactivating":
        return "stopping", "Omarchy VM is shutting down"
    guest = st["guest"]
    if guest == "running":
        return ("running", "Omarchy VM is reachable") if st["sshReady"] else ("starting", "Omarchy VM is starting")
    if guest in ("paused", "suspended"):
        return "paused", "Omarchy VM is paused"
    if guest == "shutdown":
        return "stopping", "Omarchy VM is shutting down"
    if guest in ("prelaunch", "inmigrate", "restore-vm"):
        return "starting", "Omarchy VM is starting"
    if guest in ("guest-panicked", "internal-error", "io-error", "watchdog"):
        return "error", "Omarchy VM needs attention; open it or force it off"
    if st.get("unitState") == "activating":
        return "starting", "Omarchy VM is starting"
    # QEMU is running (it uses RAM) but its monitor gave no usable state.
    return "error", "Omarchy VM is running; its monitor did not respond"


def summary(machine, op):
    return classify(status(machine), op)


def parse_probe(text):
    values = {}
    for line in str(text).splitlines()[:20]:
        key, _, value = line.strip().partition("=")
        if key in ("cpu", "memoryKiB", "disk", "free") and re.fullmatch(r"\d{1,20}", value):
            values[key] = int(value)
        elif key == "desktop" and value in ("available", "unavailable"):
            values[key] = value
    if "desktop" not in values:
        raise ValueError("Invalid guest result")
    memory = values.get("memoryKiB")
    return {"cpu": values.get("cpu") or None, "memoryBytes": memory*1024 if memory else None,
            "diskBytes": values.get("disk"), "diskFreeBytes": values.get("free"), "desktop": values["desktop"]}


def guest_probe(machine):
    ssh = executable(machine["connections"]["ssh"])
    if not ssh:
        raise OperationError("Omarchy VM SSH command is missing")
    return parse_probe(run([ssh, PROBE], 15))


def labels():
    data = read_json(state_dir()/"labels.json", {})
    rows = data.get(ID, {}) if isinstance(data, dict) else {}
    return {k: v for k, v in rows.items() if BACKUP_ID.fullmatch(str(k)) and label(v)} if isinstance(rows, dict) else {}


def backups(machine):
    rows = json.loads(run([control(machine), "backups", "--json"], 10))
    if not isinstance(rows, list):
        raise ValueError("Invalid backup list")
    names, result = labels(), []
    for r in rows[:200]:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not BACKUP_ID.fullmatch(r["id"]):
            continue
        verified = r.get("verified") is True
        result.append({"id": r["id"], "label": names.get(r["id"]) or label(r.get("label")) or "Omarchy VM backup",
                       "createdAt": stamp(r.get("createdAt")), "bytes": number(r.get("bytes")), "verified": verified,
                       "verifiedAt": stamp(r.get("verifiedAt")) if verified else "",
                       "verificationSource": "SHA-256 and archive contents checked" if verified else "Integrity not verified",
                       "restoreTested": r.get("restoreTested") is True, "restoreTestedAt": stamp(r.get("restoreTestedAt"))})
    return sorted(result, key=lambda r: r["createdAt"], reverse=True)[:30]


def allocation(machine):
    raw = json.loads(run([control(machine), "resources"], 8))
    if not isinstance(raw, dict) or not number(raw.get("cpus"), 1) or not re.fullmatch(r"[1-9][0-9]?G", str(raw.get("memory"))):
        raise ValueError("Invalid allocation")
    return {"cpu": raw["cpus"], "ram": raw["memory"]}


def capabilities(machine):
    ctl = bool(executable(machine["connections"]["control"]))
    # No Aside, recovery console, console handoff, administrator helper or
    # offline boot rehearsal exist for this provider; the panel hides them.
    return {"viewer": ctl, "lifecycle": ctl, "ssh": bool(executable(machine["connections"]["ssh"])),
            "cua": bool(executable(machine["connections"]["cua"])), "shared": True, "backup": ctl, "verify": ctl,
            "restore": ctl, "allocate": ctl, "aside": False, "asideSupported": False, "console": False,
            "handoff": False, "admin": False, "catalogue": False, "rehearsal": False, "rollback": False}


def collect(config, machine, deep=False, previous=None):
    op = operation()
    s = {"schemaVersion": 1, "checkedAt": now(), "demo": False, "capabilities": capabilities(machine), "operation": op,
         "checks": {}, "resources": {}, "error": "", "backups": [], "allocation": {}, "storage": {},
         "sharedPath": machine["sharedPath"], "viewerOpen": False, "maintenance": None}
    try:
        usage = shutil.disk_usage(machine["storagePath"])
        s["storage"] = {"availableBytes": usage.free, "totalBytes": usage.total}
    except OSError:
        pass
    # Catalogue and allocation are read on deep checks; shallow polls run only
    # status --json and keep the previous values for the same machine.
    if previous:
        s["backups"], s["allocation"] = previous.get("backups", []), previous.get("allocation", {})
    try:
        st = status(machine)
    except (OperationError, ValueError, OSError):
        # Report the failure as this machine's state rather than failing the
        # snapshot, which keeps its identity and the switcher to other machines.
        s["state"], s["summary"] = "unknown", "Omarchy VM status is unavailable"
        s["error"] = "The omarchy-vm status check did not respond"
        for name in ("ssh", "desktop", "cua"):
            s["checks"][name] = check("unknown", "VM status unavailable")
        return s
    s["viewerOpen"] = st["viewerOpen"]
    s["vmDiskBytes"] = st["diskUsedBytes"]
    s["state"], s["summary"] = classify(st, op)
    s["maintenance"] = st["maintenance"] if outside(st, op) else None
    if deep or not previous:
        for key, read in (("backups", backups), ("allocation", allocation)):
            try:
                s[key] = read(machine)
            except (OperationError, ValueError, OSError):
                pass
    if s["maintenance"] or s["state"] not in ("running", "starting"):
        message = ("Unavailable during this operation" if s["maintenance"] or s["summary"].endswith("in progress") else
                   "Start Omarchy VM to check its connections" if s["state"] == "stopped" else s["summary"])
        for name in ("ssh", "desktop", "cua"):
            s["checks"][name] = check("offline", message)
        return s
    if not st["sshReady"]:
        s["checks"].update(ssh=check("offline", "Waiting for the guest SSH server"),
                           desktop=check("unknown", "Desktop state unavailable"), cua=check("unknown", "Waiting for the guest"))
        return s
    if not deep:
        if previous:
            s["error"] = previous.get("error", "")
            s["checks"], s["resources"] = carried_checks(previous), previous.get("resources", {})
            if s["error"]:
                s["summary"] = "Omarchy VM is reachable; connection checks failed"
            elif previous.get("state") == "running" and not previous.get("maintenance") and fresh_checks(previous, config["pollSeconds"]):
                s["summary"] = previous.get("summary", s["summary"])
            else:
                s["summary"] = "Omarchy VM is reachable; connection checks are due"
        return s
    s["checks"]["ssh"] = check("missing", "SSH connection is not configured")
    if not s["capabilities"]["ssh"]:
        return s
    try:
        guest = guest_probe(machine)
    except (OperationError, ValueError, OSError):
        s["checks"]["ssh"] = check("error", "SSH did not respond. Open the VM and check its network.")
        s["checks"]["desktop"] = check("unknown", "Desktop state unavailable")
        return s
    s["resources"] = {k: guest[k] for k in ("cpu", "memoryBytes", "diskBytes", "diskFreeBytes")}
    s["checks"]["ssh"] = check("ready", "Command access ready; agents have passwordless sudo in the guest")
    desktop = guest["desktop"] == "available"
    s["checks"]["desktop"] = check("available", "Hyprland desktop session available") if desktop else \
        check("signed-out", "Hyprland session is not running yet")
    s["summary"] = "Omarchy VM is ready" if desktop else "Omarchy VM is waiting for its desktop session"
    s["checks"]["cua"] = check("missing", "Cua connection is not configured")
    if s["capabilities"]["cua"]:
        s["checks"]["cua"] = cua_check(executable(machine["connections"]["cua"]), "Cua did not respond. Check the guest desktop session.") \
            if desktop else check("offline", "Waiting for the desktop session")
    return s


# Operations. The caller holds the operation lock and validated parameters.

class Failed(OperationError):
    # A failed omarchy-vm command, with what it reported before failing.
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def reason(lines, lead=""):
    # omarchy-vm reports failures as "omarchy-vm: <reason>". Return a short
    # reason without paths, after an expected lead-in; never other output.
    for line in reversed(lines):
        if line.startswith("omarchy-vm: "):
            text = line[12:].strip()
            text = text[len(lead):].strip() if lead and text.startswith(lead) else text
            if text and len(text) <= 160 and "/" not in text and all(c.isprintable() for c in text):
                return text.rstrip(".")
    return ""


def sanitize(lines, fallback):
    text = reason(lines)
    return text[0].upper() + text[1:] if text else fallback


def stream(action, argv, fallback, echo=False, progress=None):
    # omarchy-vm never needs the operation terminal: it runs detached, so
    # closing that terminal or Ctrl+C there cannot interrupt it.
    from operations import provider, record, say
    report = {"lines": [], "stages": [], "forced": False, "saved": ""}
    with provider(argv, detach=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                  text=True, errors="replace", bufsize=1) as p:
        for raw in p.stdout:
            if echo:
                say(raw[:1000], end="" if raw.endswith("\n") else "\n")
            line = raw.strip()[:300]
            if line.startswith("OMACHINES_STAGE:"):
                stage = line.partition(":")[2]
                if stage in STAGES:
                    report["stages"].append(stage)
                    record(action, "running", STAGES[stage])
            elif line:
                for prefix, message in (progress or {}).items():
                    if line.startswith(prefix):
                        record(action, "running", message)
                report["forced"] = report["forced"] or line.startswith("Forced off:")
                report["saved"] = line if SAVED.fullmatch(line) else report["saved"]
                report["lines"] = (report["lines"] + [line])[-4:]
    if p.returncode:
        raise Failed(sanitize(report["lines"], fallback), report)
    return report


def focus(address):
    run(["hyprctl", "dispatch", 'hl.dsp.focus({ window = "address:'+address+'" })'], 3)


def guide(machine):
    c = machine["connections"]
    return ("Omachines connection guide: " + machine["name"] + "\n\n"
            "Guest commands (one command as the guest user): " + c["ssh"] + " '<command>'\n"
            "Guest desktop MCP (Cua Driver, stdio): " + c["cua"] + "\n"
            "VM control (status, start, stop, view, backups): " + c["control"] + "\n\n"
            "Configure these existing commands in your chosen agent; no passwords belong in MCP settings.\n"
            "The guest signs in to Hyprland automatically and does not idle-lock. Desktop tools need that session;\n"
            "check it in the Agents section before GUI work.\n"
            "Agents get passwordless sudo inside the guest. That is root in the VM only, not on this host,\n"
            "but they can read and write the shared folder " + machine["sharedPath"] + " (~/Shared in the guest)\n"
            "and use any accounts signed in inside the guest.\n")


def perform(action, config, parameters, machine):
    from operations import record, say
    ctl = control(machine)
    if action == "open":
        windows = hypr_windows(lambda c: c.get("class") == "remote-viewer" and str(c.get("title", "")).startswith("Omarchy VM"))
        if windows:
            focus(windows[0])
            return "Omarchy VM viewer focused"
        record(action, "running", "Starting Omarchy VM and opening its viewer")
        stream(action, [ctl, "view"], "The viewer could not be opened")
        return "Omarchy VM viewer requested"
    if action == "start":
        stream(action, [ctl, "start"], "Omarchy VM did not start")
        return "Omarchy VM started; connections may take a moment"
    if action == "stop":
        record(action, "running", "Shutting down Omarchy VM")
        report = stream(action, [ctl, "stop"], "Omarchy VM did not shut down; check it before forcing it off")
        return "Omarchy VM was forced off; it did not shut down cleanly" if report["forced"] else "Omarchy VM shut down"
    if action == "shared":
        path = Path(machine["sharedPath"])
        if not path.is_dir():
            raise OperationError("Shared folder does not exist")
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return "Shared folder opened"
    if action == "backup":
        record(action, "running", "Preparing a complete Omarchy VM backup")
        try:
            report = stream(action, [ctl, "backup"], "Backup did not finish; Omarchy VM may need starting",
                            progress={"Shutting down": "Shutting down Omarchy VM for a consistent copy"})
        except Failed as failure:
            # After the saved line the backup is complete; only the restart failed.
            if not failure.report["saved"]:
                raise
            raise OperationError(failure.report["saved"] + ", but Omarchy VM did not start again: " +
                                 (reason(failure.report["lines"], "backup saved, but the VM did not start again:")
                                  or "no reason was reported")) from None
        if not report["saved"]:
            raise OperationError("Backup completion could not be confirmed; check the backup list")
        return (report["saved"] + (" (the VM had to be forced off first)" if report["forced"] else "") +
                ("; Omarchy VM is starting again" if "starting" in report["stages"] else ""))
    if action == "verify":
        record(action, "running", "Checking backup integrity")
        stream(action, [ctl, "verify", parameters["backupId"]], "Backup integrity check failed")
        return "Backup checksum and contents verified"
    if action == "label":
        known = {r["id"] for r in backups(machine)}
        if parameters["backupId"] not in known:
            raise OperationError("Select a backup before naming it")
        data = read_json(state_dir()/"labels.json", {})
        data = data if isinstance(data, dict) else {}
        data[ID] = {**labels(), parameters["backupId"]: parameters["label"]}
        write_json(state_dir()/"labels.json", data)
        return "Backup name saved"
    if action == "allocate":
        host = os.cpu_count() or 1
        total = os.sysconf("SC_PAGE_SIZE")*os.sysconf("SC_PHYS_PAGES")
        cpu, ram = parameters["cpu"], parameters["ram"]
        # omarchy-vm accepts at most 16 CPUs and 99G; leave 4 GiB for the host.
        if not 1 <= cpu <= min(16, host) or not 4 <= ram <= 99 or ram*1024**3 > total-4*1024**3:
            raise OperationError("Choose 1-%d CPUs and 4-%d GiB RAM" % (min(16, host), max(4, min(99, total//1024**3-4))))
        record(action, "running", "Saving allocation; Omarchy VM restarts if it is running")
        stream(action, [ctl, "resources", "--cpus", str(cpu), "--memory", f"{ram}G"], "Allocation was not applied")
        return f"Allocation saved: {cpu} CPUs, {ram} GiB RAM"
    if action == "restore":
        name = parameters["backupId"]
        row = next((r for r in backups(machine) if r["id"] == name), None)
        if row is None:
            raise OperationError("Select a known backup")
        record(action, "running", "Waiting for typed confirmation")
        say("\nOmachines: restore Omarchy VM\n")
        say("Backup: " + row["label"] + " · " + (row["createdAt"] or "unknown date") +
            (" · integrity verified" if row["verified"] else " · not verified"))
        say("Omarchy VM stops and its disk and firmware variables are replaced. The current disk is kept")
        say("aside next to the VM storage. Shared-folder files and host settings are not changed.\n")
        say("Type RESTORE " + name + " to continue:")
        try:
            typed = input().strip()
        except (EOFError, OSError):
            typed = ""
        if typed != "RESTORE " + name:
            raise OperationError("Restore cancelled; Omarchy VM is unchanged")
        record(action, "running", "Verifying the backup before replacement")
        try:
            stream(action, [ctl, "restore", name, "--replace-current"], "Restore did not finish; check this terminal before retrying",
                   echo=True, progress={"Shutting down": "Shutting down Omarchy VM"})
        except Failed as failure:
            # Other failures leave the current disk in place.
            if "restored" not in failure.report["stages"]:
                raise
            raise OperationError("Omarchy VM was restored from " + name + " but did not start: " +
                                 (reason(failure.report["lines"], "restored, but the VM did not start again:") or "no reason was reported") +
                                 ". The previous disk was kept aside") from None
        return "Omarchy VM restored from " + name + "; the previous disk was kept aside"
    if action == "connections":
        folder = state_dir()/"reports"
        folder.mkdir(exist_ok=True, mode=0o700)
        path = folder/"connections-omarchy.txt"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(guide(machine))
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return "Private connection instructions opened"
    raise OperationError("This action is not available for " + machine["name"])
