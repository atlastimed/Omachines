"""Versioned user-owned machine registry; provider-specific values stay here."""
import os
import json
from pathlib import Path
import re

DEFAULT_ID = "windows"


def default_machine():
    home = Path.home()
    return {"id": DEFAULT_ID, "name": "Windows 11", "provider": "omarchy-windows",
            "host": "127.0.0.1", "sshPort": 2222, "consolePort": 8006,
            "storagePath": str(home/".windows"), "sharedPath": str(home/"Windows"),
            "backupPath": str(home/"Windows-VM-Backups"),
            "connections": {kind: str(home/".local/bin"/name) for kind,name in
                            {"viewer":"windows-vm-launch", "ssh":"windows-vm-ssh",
                             "cua":"windows-vm-mcp", "aside":"windows-vm-aside-mcp"}.items()}}


def validate(machine):
    if not isinstance(machine, dict) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", str(machine.get("id", ""))):
        raise ValueError("Invalid machine identity")
    if not isinstance(machine.get("name"), str) or not 1 <= len(machine["name"]) <= 60 or any(ord(c)<32 for c in machine["name"]):
        raise ValueError("Invalid machine name")
    if machine.get("provider") != "omarchy-windows" or machine["id"] != DEFAULT_ID:
        raise ValueError("This release supports the existing Omarchy Windows provider only")
    # These settings describe current connections, never arbitrary privileged paths.
    expected=default_machine()
    for key in ("host", "sshPort", "consolePort", "storagePath", "sharedPath", "backupPath", "connections"):
        if machine.get(key) != expected[key]:
            raise ValueError("Custom connection providers are not supported by this release")
    return dict(machine)


def load():
    from core import state_dir
    path=state_dir()/"machines.json"
    try:
        if path.is_symlink(): raise ValueError("Machine registry must not be a symlink")
        raw=json.loads(path.read_text())
    except FileNotFoundError:
        return {"schemaVersion":1,"selected":DEFAULT_ID,"machines":[default_machine()]}
    except (OSError,json.JSONDecodeError):
        raise ValueError("Machine registry could not be read; existing file was preserved") from None
    if not isinstance(raw,dict) or raw.get("schemaVersion")!=1 or raw.get("selected")!=DEFAULT_ID or not isinstance(raw.get("machines"),list) or len(raw["machines"])!=1:
        raise ValueError("Unsupported machine registry; existing file was preserved")
    return {"schemaVersion":1,"selected":DEFAULT_ID,"machines":[validate(raw["machines"][0])]}


def selected():
    return load()["machines"][0]


def initialize():
    from core import write_json, state_dir
    data=load()
    if not (state_dir()/"machines.json").exists():
        write_json(state_dir()/"machines.json", data)
    return data


def admin_state():
    from core import read_json
    raw=read_json(Path("/var/lib/omachines/users")/str(os.getuid())/"state.json", {})
    return raw if isinstance(raw,dict) else {}


def host_resources():
    values={}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k,_,v=line.partition(":")
        if k in ("MemTotal","MemAvailable"):
            values[k]=int(v.split()[0])*1024
    return {"cpu":os.cpu_count() or 1,"memoryBytes":values.get("MemTotal",0),"availableBytes":values.get("MemAvailable",0)}
