#!/usr/bin/env python3
"""Install/update/remove the local Omachines plugin; preserve machines and data."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
ID = "atlastimed.omachines"
DOCS = ("README.md", "LICENSE", "INTEGRATION.md", "SECURITY.md",
        "CONTRIBUTING.md", "VALIDATION.md", "REVIEW-FIXES.md")
FILES = ("manifest.json", "BarWidget.qml", "MachineView.qml", "StatusRow.qml",
         "Service.qml", "Model.js", "BackupControls.qml", "ResourceControls.qml", *DOCS)
BACKEND = ("core.py", "operations.py", "mcp.py", "probes.py", "omachinesctl", "guest-status.ps1", "machines.py", "history.py", "admin.py", "aside-cli.ps1")


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
    if run("omarchy-shell", "shell", method, *args) != "ok":
        raise RuntimeError("Plugin files are updated, but shell settings need attention")


def rescan(present):
    run("omarchy-shell", "shell", "rescanPlugins")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        catalog = json.loads(run("omarchy", "plugin", "list", "--json"))
        if any(p.get("id") == ID for p in catalog) == present:
            return
        time.sleep(0.1)
    raise RuntimeError("The shell did not finish scanning the plugin")


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
    # installed files and current settings rather than guessing a rollback.
    if args.uninstall or not had_plugin or args.setup_workspace:
        read_config(shell)
    if args.uninstall:
        shell_mutation("setPluginEnabled", ID, "false")
    elif not had_plugin:
        shell_mutation("putBarWidget", ID, json.dumps({"section":"right", "index":1}))
    if not args.uninstall and args.setup_workspace:
        shell_mutation("setBarWidget", ID, "setupWorkspace", json.dumps(str(args.setup_workspace)), "{}")
    print("Omachines removed; VM, backups and private state preserved." if args.uninstall else "Omachines installed. Click VM on the right of the bar.")
    print("Previous configuration: " + str(backup))


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
