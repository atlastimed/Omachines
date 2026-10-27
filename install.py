#!/usr/bin/env python3
"""Install/update/remove the local Omachines plugin; preserve machines and data."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
ID = "atlastimed.omachines"
DOCS = ("README.md", "LICENSE", "INTEGRATION.md", "SECURITY.md",
        "CONTRIBUTING.md", "VALIDATION.md", "VALIDATION-v0.2.md", "VALIDATION-v0.3.md", "REVIEW-FIXES.md")
FILES = ("manifest.json", "BarWidget.qml", "MachineView.qml", "StatusRow.qml",
         "Service.qml", "Model.js", "BackupControls.qml", "ResourceControls.qml", *DOCS)
BACKEND = ("core.py", "operations.py", "omarchy.py", "mcp.py", "probes.py", "omachinesctl", "guest-status.ps1", "machines.py", "history.py", "admin.py", "aside-cli.ps1", "supervise.py")
STATE = "installer.json"
SECTIONS = ("left", "center", "right")
DEFAULT_PLACEMENT = {"section": "right", "index": 1}
HELPER = Path("/usr/local/lib/omachines/admin.py")


def run(*args):
    result = subprocess.run(args, check=True, text=True, capture_output=True, timeout=30)
    return result.stdout.strip()


def atomic(path, text):
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", delete=False) as f:
        temp = Path(f.name)
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    try:
        if path.exists():
            shutil.copymode(path, temp)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def read_config(path):
    # No shell.json replacement, including rollback. Reject unsupported or
    # malformed files before touching the installation; the shell owns edits.
    if path.is_symlink():
        raise ValueError("Symlinked shell.json is not supported; no changes made")
    text = path.read_text() if path.exists() else "{}"
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("bar", {}), dict):
        raise ValueError("Invalid shell configuration")
    layout = data.get("bar", {}).get("layout", {})
    if not isinstance(layout, dict) or any(not isinstance(v, list) for v in layout.values()):
        raise ValueError("Invalid shell bar layout")
    if not isinstance(data.get("plugins", []), list):
        raise ValueError("Invalid shell plugin settings")
    return text


def shell_mutation(method, *args):
    # These IPC methods mutate the shell's current settings in its event loop.
    # No saved entry or whole-file snapshot is written over concurrent edits.
    result = run("omarchy-shell", "shell", method, *args)
    if result != "ok":
        raise RuntimeError(f"The shell rejected {method}: {result[:200]}")


def plugin_options(values):
    # Only this plugin's manifest options with plausible types. Saved options
    # are user-writable state, restored one field at a time by the shell.
    schema = json.loads((ROOT/"manifest.json").read_text())["barWidget"]["schema"]
    valid = {"boolean": lambda v, s: isinstance(v, bool),
             "integer": lambda v, s: type(v) is int and s.get("min", v) <= v <= s.get("max", v),
             "string": lambda v, s: isinstance(v, str) and len(v) <= 4096 and not set(v) & {"\0", "\n"}}
    values = values if isinstance(values, dict) else {}
    return {s["key"]: values[s["key"]] for s in schema
            if s["key"] in values and valid.get(s["type"], lambda *_: False)(values[s["key"]], s)}


def bar_placement(value):
    # Native placement: after the saved neighbour if it is still in that
    # section, otherwise the saved index (the shell clamps it).
    if (not isinstance(value, dict) or value.get("section") not in SECTIONS
            or type(value.get("index")) is not int or not 0 <= value["index"] <= 1000):
        return None
    result = {"section": value["section"], "index": value["index"]}
    if isinstance(value.get("after"), str) and 0 < len(value["after"]) <= 256:
        result["after"] = value["after"]
    return result


def bar_entry(text):
    # The plugin's current bar entry (raw) and its placement, or (None, None).
    layout = json.loads(text).get("bar", {}).get("layout", {})
    for section in SECTIONS:
        row = layout.get(section, [])
        ids = [e.get("id") if isinstance(e, dict) else e for e in row]
        if ID in ids:
            index = ids.index(ID)
            after = ids[index-1] if index else None
            entry = row[index] if isinstance(row[index], dict) else {"id": ID}
            return entry, bar_placement({"section": section, "index": index, "after": after})
    return None, None


def load_state():
    # Installer-owned private state: a pending first activation, and options
    # and placement saved by uninstall until a later activation restores them.
    from core import read_json, state_dir
    data = read_json(state_dir()/STATE, {})
    data = data if isinstance(data, dict) else {}
    return {"activationPending": data.get("activationPending") is True,
            "options": plugin_options(data.get("options")), "placement": bar_placement(data.get("placement"))}


def save_state(state):
    from core import state_dir, write_json
    state = {key: value for key, value in state.items() if value}
    if state:
        write_json(state_dir()/STATE, state)
    else:
        (state_dir()/STATE).unlink(missing_ok=True)


def in_bar():
    return any(p.get("id") == ID and p.get("enabled") is True
               for p in json.loads(run("omarchy", "plugin", "list", "--json")))


def activate(shell, state):
    # putBarWidget leaves an entry that is already on the bar where it is, so
    # a retry after a lost acknowledgement cannot add a second entry.
    read_config(shell)
    shell_mutation("putBarWidget", ID, json.dumps(state["placement"] or DEFAULT_PLACEMENT))
    if not in_bar():
        raise RuntimeError("The shell did not add the bar entry")
    # Saved options fill only fields the entry lacks; values changed since the
    # entry was added are never overwritten by an older saved copy. The entry
    # is read again before each field, so an edit made while earlier fields
    # were restored is kept. The shell has no set-if-absent call: only an edit
    # landing between that read and its write could still be replaced.
    for key, value in state["options"].items():
        entry, _ = bar_entry(read_config(shell))
        if key not in (entry or {}):
            shell_mutation("setBarWidget", ID, key, json.dumps(value), "{}")
    save_state({})


def rescan(present):
    run("omarchy-shell", "shell", "rescanPlugins")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        catalog = json.loads(run("omarchy", "plugin", "list", "--json"))
        if any(p.get("id") == ID for p in catalog) == present:
            return
        time.sleep(0.1)
    raise RuntimeError("The shell did not finish scanning the plugin")


def helper_version(path):
    # The VERSION="x.y.z" line of an administrator helper, as numbers.
    try:
        match = re.search(r'(?m)^VERSION="(\d{1,4})\.(\d{1,4})\.(\d{1,4})"$', path.read_text())
    except (OSError, UnicodeError):
        return None
    return tuple(map(int, match.groups())) if match else None


def helper_notice():
    # The optional root helper is installed separately; say when it is older.
    installed, release = helper_version(HELPER), helper_version(ROOT/"backend/admin.py")
    if not installed or not release or installed >= release:
        return ""
    return (f"The administrator helper is {'.'.join(map(str, installed))}; this release includes "
            f"{'.'.join(map(str, release))}. Reinstall it: sudo /usr/bin/python3 -I scripts/install-admin.py")


def stage_release(staged):
    # Some installed Quickshell versions retain cached QML after rescan.
    # Content-addressed entry paths load fresh components without restarting
    # the user's shell. There is only one runtime in the installed directory.
    files = [ROOT/f for f in FILES if f not in ("manifest.json", *DOCS)]
    files += [ROOT/"backend"/f for f in BACKEND]
    digest = hashlib.sha256()
    for file in sorted(files):
        digest.update(str(file.relative_to(ROOT)).encode()+b"\0"+file.read_bytes())
    relative = Path("runtime")/("r"+digest.hexdigest()[:16])
    runtime = staged/relative
    (runtime/"backend").mkdir(parents=True)
    for file in files:
        shutil.copy2(file, runtime/file.relative_to(ROOT))
    for file in DOCS:
        shutil.copy2(ROOT/file, staged/file)
    manifest = json.loads((ROOT/"manifest.json").read_text())
    manifest["entryPoints"] = {key:str(relative/value) for key,value in manifest["entryPoints"].items()}
    (staged/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")


def install(args):
    config = Path.home()/".config/omarchy"
    destination = config/"plugins"/ID
    shell = config/"shell.json"
    from core import private_dir, state_dir
    if destination.is_symlink():
        raise ValueError("Refusing to replace a symlinked plugin directory")
    if destination.exists() and json.loads((destination/"manifest.json").read_text()).get("id") != ID:
        raise ValueError("Destination is not the Omachines plugin")
    old_config = read_config(shell)
    if args.setup_workspace:
        args.setup_workspace = args.setup_workspace.expanduser().resolve(strict=True)
        if not (args.setup_workspace/"create-development-backup.sh").is_file():
            raise ValueError("The setup workspace does not contain the backup provider")
    run("omarchy-shell", "shell", "ping")
    if not args.uninstall:
        run("omarchy", "plugin", "validate", str(ROOT))
        for file in [*(ROOT/f for f in FILES), *(ROOT/"backend"/f for f in BACKEND)]:
            if not file.is_file() or file.is_symlink():
                raise ValueError("Missing or symlinked release file: " + str(file))
    backup = private_dir(state_dir()/"install-backups"/dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    atomic(backup/"shell.json", old_config)
    had_plugin = destination.exists()
    # A fresh install, or one whose first activation never finished, adds the
    # bar entry. An ordinary update leaves a deliberately removed entry off.
    # The intent is saved before publishing so a retry can complete it.
    state = load_state()
    pending = not args.uninstall and (not had_plugin or state["activationPending"])
    if pending and not state["activationPending"]:
        state["activationPending"] = True
        save_state(state)
    staged = None
    published = False
    try:
        if not args.uninstall:
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged = Path(tempfile.mkdtemp(prefix=".omachines-stage-", dir=config))
            stage_release(staged)
            run("omarchy", "plugin", "validate", str(staged))
        if had_plugin:
            shutil.move(destination, backup/ID)
        if staged:
            staged.replace(destination)
            staged = None
            published = True
        # Omarchy reloadPlugins unloads ordinary services itself. Temporarily
        # disabling the widget would remove settings and require a stale restore.
        rescan(not args.uninstall)
    except BaseException:
        if published and destination.exists():
            shutil.move(destination, backup/"failed-install")
        if (backup/ID).exists():
            shutil.move(backup/ID, destination)
        subprocess.run(["omarchy-shell", "shell", "rescanPlugins"], capture_output=True, timeout=15)
        raise
    finally:
        if staged:
            shutil.rmtree(staged)
    # File transaction is complete. Native settings mutations are separate,
    # current-state operations. If an IPC acknowledgement fails, retain the
    # installed files and current settings rather than guessing a rollback;
    # rerunning the same command resumes from the saved installer state.
    try:
        if args.uninstall:
            entry, placement = bar_entry(read_config(shell))
            # Disabling deletes the entry with its options, so save them first.
            # A repeated removal (entry already gone) keeps the earlier copy.
            if entry is not None:
                state["options"] = {**state["options"], **plugin_options(entry)}
                state["placement"] = placement
            state["activationPending"] = False
            save_state(state)
            shell_mutation("setPluginEnabled", ID, "false")
        elif pending:
            activate(shell, state)
        if not args.uninstall and args.setup_workspace:
            read_config(shell)
            shell_mutation("setBarWidget", ID, "setupWorkspace", json.dumps(str(args.setup_workspace)), "{}")
    except Exception as error:
        raise RuntimeError(f"Omachines files are {'removed' if args.uninstall else 'installed'}, but its bar "
                           f"settings did not finish updating ({error}). Run the same install.py command again to finish.") from error
    print("Omachines removed; VM, backups, private state and plugin options preserved." if args.uninstall
          else "Omachines installed. Click VM in the bar." if pending
          else "Omachines updated; its bar entry was left as it was.")
    print("Previous configuration: " + str(backup))
    notice = "" if args.uninstall else helper_notice()
    if notice:
        print(notice)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--setup-workspace", type=Path)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT/"backend"))
    from machines import initialize
    from core import maintenance
    with maintenance():
        initialize()
        install(args)


if __name__ == "__main__":
    main()
