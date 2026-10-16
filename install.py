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
        "CONTRIBUTING.md", "VALIDATION.md")
FILES = ("manifest.json", "BarWidget.qml", "MachineView.qml", "StatusRow.qml",
         "Service.qml", "Model.js", *DOCS)
BACKEND = ("core.py", "operations.py", "mcp.py", "omachinesctl", "guest-status.ps1")


def run(*args):
    result = subprocess.run(args, check=True, text=True, capture_output=True)
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


def set_workspace(config, workspace):
    for section in config.get("bar", {}).get("layout", {}).values():
        if isinstance(section, list):
            for item in section:
                if isinstance(item, dict) and item.get("id") == ID:
                    item["setupWorkspace"] = str(workspace)


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--setup-workspace", type=Path)
    args = parser.parse_args()
    config = Path.home()/".config/omarchy"
    destination = config/"plugins"/ID
    shell = config/"shell.json"
    sys.path.insert(0, str(ROOT/"backend"))
    from core import busy, private_dir, state_dir
    if busy():
        raise SystemExit("Wait for the Omachines operation to finish before updating or removing it.")
    if destination.is_symlink():
        raise SystemExit("Refusing to replace a symlinked plugin directory.")
    if destination.exists() and json.loads((destination/"manifest.json").read_text()).get("id") != ID:
        raise SystemExit("Destination is not the Omachines plugin.")
    if args.setup_workspace:
        args.setup_workspace = args.setup_workspace.expanduser().resolve(strict=True)
        if not (args.setup_workspace/"create-development-backup.sh").is_file():
            raise SystemExit("The setup workspace does not contain the backup provider.")
    run("omarchy-shell", "shell", "ping")
    if not args.uninstall:
        run("omarchy", "plugin", "validate", str(ROOT))
        for file in [*(ROOT/f for f in FILES), *(ROOT/"backend"/f for f in BACKEND)]:
            if not file.is_file() or file.is_symlink():
                raise SystemExit("Missing or symlinked release file: " + str(file))
    backup = private_dir(state_dir()/"install-backups"/dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    had_config = shell.exists()
    old_config = shell.read_text() if had_config else "{}"
    atomic(backup/"shell.json", old_config)
    had_plugin = destination.exists()
    staged = None
    published = False
    was_enabled = False
    try:
        if had_plugin:
            catalog = json.loads(run("omarchy", "plugin", "list", "--json"))
            was_enabled = any(p.get("id") == ID and p.get("enabled") for p in catalog)
            if was_enabled:
                run("omarchy", "plugin", "disable", ID)
                # Give the shell time to dispose its existing service. Keeping
                # it alive across a code update would keep the old Python code.
                time.sleep(0.5)
        if args.uninstall:
            if had_plugin:
                shutil.move(destination, backup/ID)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged = Path(tempfile.mkdtemp(prefix=".omachines-stage-", dir=config))
            stage_release(staged)
            run("omarchy", "plugin", "validate", str(staged))
            if had_plugin:
                shutil.move(destination, backup/ID)
            staged.replace(destination)
            staged = None
            published = True
        run("omarchy-shell", "shell", "rescanPlugins")
        if not args.uninstall and had_plugin:
            atomic(shell, old_config)
            run("omarchy-shell", "shell", "rescanPlugins")
        if not args.uninstall and not had_plugin:
            run("omarchy", "plugin", "enable", ID, "--section", "right", "--index", "1")
        if not args.uninstall and args.setup_workspace:
            data = json.loads(shell.read_text())
            set_workspace(data, args.setup_workspace)
            atomic(shell, json.dumps(data, indent=2)+"\n")
        print("Omachines removed; VM, backups and private state preserved." if args.uninstall else "Omachines installed. Click VM on the right of the bar.")
        print("Previous configuration: " + str(backup))
    except Exception:
        atomic(shell, old_config) if had_config else shell.unlink(missing_ok=True)
        if published and destination.exists():
            shutil.move(destination, backup/"failed-install")
        if (backup/ID).exists():
            shutil.move(backup/ID, destination)
        subprocess.run(["omarchy-shell", "shell", "rescanPlugins"], capture_output=True)
        raise
    finally:
        if staged:
            shutil.rmtree(staged)


if __name__ == "__main__":
    main()
