"""Versioned user-owned machine registry; provider-specific values stay here."""
import fcntl
import os
import json
from pathlib import Path
import re

SCHEMA = 2
MEMINFO = Path("/proc/meminfo")
DEFAULT_ID = "windows"
WINDOWS = "omarchy-windows"
OMARCHY = "omarchy-qemu"


def windows_machine():
    home = Path.home()
    return {"id": DEFAULT_ID, "name": "Windows 11", "provider": WINDOWS,
            "host": "127.0.0.1", "sshPort": 2222, "consolePort": 8006,
            "storagePath": str(home/".windows"), "sharedPath": str(home/"Windows"),
            "backupPath": str(home/"Windows-VM-Backups"),
            "connections": {kind: str(home/".local/bin"/name) for kind,name in
                            {"viewer":"windows-vm-launch", "ssh":"windows-vm-ssh",
                             "cua":"windows-vm-mcp", "aside":"windows-vm-aside-mcp"}.items()}}


def omarchy_machine():
    # Guest SSH goes over vsock through the omarchy-vm-ssh wrapper: no host port.
    home = Path.home()
    return {"id": "omarchy", "name": "Omarchy VM", "provider": OMARCHY, "storagePath": "/mnt/windows-vm/omarchy",
            "sharedPath": str(home/"OmarchyVM"), "backupPath": str(home/"Omarchy-VM-Backups"),
            "connections": {kind: str(home/".local/bin"/name) for kind,name in
                            {"control":"omarchy-vm", "ssh":"omarchy-vm-ssh", "cua":"omarchy-vm-mcp"}.items()}}


default_machine = windows_machine
# Each provider has one fixed machine. Connections are expected paths, never
# user-supplied commands: they always come from this definition, never the file.
PROVIDERS = {WINDOWS: windows_machine, OMARCHY: omarchy_machine}


def validate(machine):
    # Only the identity is read from the file, and a valid display name kept;
    # every other field is the provider's definition in this release.
    if not isinstance(machine, dict) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", str(machine.get("id", ""))):
        raise ValueError("Invalid machine identity")
    factory = PROVIDERS.get(machine["provider"]) if isinstance(machine.get("provider"), str) else None
    if not factory or machine["id"] != factory()["id"]:
        raise ValueError("This release supports the Omarchy Windows and Omarchy VM providers only")
    result, name = factory(), machine.get("name")
    if isinstance(name, str) and 1 <= len(name) <= 60 and not any(ord(c)<32 for c in name):
        result["name"] = name
    return result


def complete(rows):
    ids = [m["id"] for m in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate machine identity")
    # Known machines missing from an older file are added with fixed defaults.
    found = {m["id"]: m for m in rows}
    return [found.get(f()["id"], f()) for f in PROVIDERS.values()]


def path():
    from core import state_dir
    return state_dir()/"machines.json"


def read_raw():
    target = path()
    try:
        if target.is_symlink(): raise ValueError("Machine registry must not be a symlink")
        return json.loads(target.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, UnicodeError):
        raise ValueError("Machine registry could not be read; existing file was preserved") from None


def load():
    raw = read_raw()
    if raw is None:
        return {"schemaVersion": SCHEMA, "selected": DEFAULT_ID, "machines": complete([])}
    data = canonical(raw)
    if data != raw:
        # An older or edited file is rewritten to this release's definitions,
        # keeping the selection; retried on the next load if it cannot be.
        try:
            save(data, expected=raw)
        except (OSError, ValueError):
            pass
    return data


def canonical(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("machines"), list):
        raise ValueError("Unsupported machine registry; existing file was preserved")
    if raw.get("schemaVersion") == 1:
        # Version 0.2 registry: one Windows entry (its name is kept); add Omarchy.
        if raw.get("selected") != DEFAULT_ID or len(raw["machines"]) != 1:
            raise ValueError("Unsupported machine registry; existing file was preserved")
        return {"schemaVersion": SCHEMA, "selected": DEFAULT_ID, "machines": complete([validate(raw["machines"][0])])}
    if raw.get("schemaVersion") != SCHEMA or not 1 <= len(raw["machines"]) <= len(PROVIDERS):
        raise ValueError("Unsupported machine registry; existing file was preserved")
    rows = complete([validate(m) for m in raw["machines"]])
    if raw.get("selected") not in [m["id"] for m in rows]:
        raise ValueError("Unsupported machine registry; existing file was preserved")
    return {"schemaVersion": SCHEMA, "selected": raw["selected"], "machines": rows}


def available(machine):
    # Windows reports missing wrappers through its own capabilities. The
    # Omarchy VM exists for this panel only when its control command does.
    if machine["provider"] != OMARCHY:
        return True
    control = Path(machine["connections"]["control"])
    return control.is_file() and os.access(control, os.X_OK)


def usable(data=None):
    data = data or load()
    return [m for m in data["machines"] if available(m)]


def get(machine_id, data=None):
    machine = next((m for m in usable(data) if m["id"] == machine_id), None)
    if machine is None:
        raise ValueError("That machine is not available")
    return machine


def selected(data=None):
    data = data or load()
    rows = usable(data)
    return next((m for m in rows if m["id"] == data["selected"]), rows[0])


def save(data, expected=None):
    # One writer at a time, including the service's poll thread. A migration
    # (expected) is dropped if the file changed since it was read.
    from core import write_json
    target = path()
    with open(target.with_name("machines.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        raw = read_raw()
        if expected is not None and raw != expected:
            return
        if isinstance(raw, dict) and raw.get("schemaVersion") == 1:
            # Retain the exact pre-migration registry once, for manual recovery.
            keep = target.with_name("machines-v1.json")
            if not keep.exists():
                write_json(keep, raw)
        write_json(target, data)


def initialize():
    data = load()
    if read_raw() != data:
        save(data)
    return data


def select(machine_id):
    from core import admission
    with admission(timeout=2):
        data = load()
        get(machine_id, data)
        if data["selected"] != machine_id or read_raw() != data:
            save({**data, "selected": machine_id})
    return machine_id


def admin_state():
    from core import read_json
    raw=read_json(Path("/var/lib/omachines/users")/str(os.getuid())/"state.json", {})
    return raw if isinstance(raw,dict) else {}


def host_resources():
    values={}
    for line in MEMINFO.read_text().splitlines():
        k,_,v=line.partition(":")
        if k in ("MemTotal","MemAvailable"):
            values[k]=int(v.split()[0])*1024
    return {"cpu":os.cpu_count() or 1,"memoryBytes":values.get("MemTotal",0),"availableBytes":values.get("MemAvailable",0)}
