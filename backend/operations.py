"""Explicit operations. A terminal-owned worker survives shell reloads."""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from core import (ROOT, OperationError, backups, busy, check, collect, now, operation, read_json,
                  run, settings, state_dir, viewer_windows, wrapper, write_json)
from mcp import Client

ACTIONS = {"open", "start", "stop", "backup", "console", "shared", "aside", "handoff", "diagnostics"}
TERMINAL = {"start", "stop", "backup"}
DISRUPTIVE = {"stop", "backup", "handoff"}


def record(action, status, message, **extra):
    value={"action":action,"status":status,"message":message,"updatedAt":now(),"pid":os.getpid(),**extra}
    write_json(state_dir()/"operation.json",value)
    return value


def notify(message, config):
    if config["notify"]:
        subprocess.Popen(["/usr/bin/notify-send","--app-name=Omachines","Omachines",message],
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def request_action(action, config, confirmed=False):
    if action not in ACTIONS:
        raise OperationError("Unknown action")
    if config["demo"]:
        raise OperationError("Demo mode never controls a real VM")
    if action in DISRUPTIVE and not confirmed:
        raise OperationError("Confirm this action first")
    # Reservation prevents two widgets from starting overlapping workers.
    with open(state_dir()/"request.lock","a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if busy():
            raise OperationError("Another operation is still running")
        argv=["/usr/bin/python3","-B",str(ROOT/"backend/omachinesctl"),"worker",action]
        record(action,"starting","Waiting for the operation to start",startedEpoch=time.time())
        try:
            if action in TERMINAL:
                subprocess.Popen(["/usr/bin/omarchy-launch-terminal",*argv,"--interactive"],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            else:
                subprocess.Popen(argv,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        except OSError:
            record(action,"failed","Could not start the operation")
            raise OperationError("Could not open the operation terminal") from None


def vm_priv(action):
    if action not in ("up","down"):
        raise OperationError("Invalid VM operation")
    result=subprocess.run(["sudo","/usr/bin/env",f"PKEXEC_UID={os.getuid()}",
                           "/usr/bin/omarchy-windows-vm","__priv",action],check=False)
    if result.returncode:
        raise OperationError("VM operation failed or authentication was cancelled")


def write_diagnostics(config):
    state=collect(config,deep=True)
    # Strict output allowlist: no guest paths, IPs, account IDs, windows or raw logs.
    report={"version":"0.1.0","checkedAt":state["checkedAt"],"state":state["state"],
            "checks":state["checks"],"resources":state["resources"],"capabilities":state["capabilities"],
            "backupCount":len(state["backups"]),"operationStatus":operation().get("status")}
    folder=state_dir()/"reports"
    folder.mkdir(mode=0o700,exist_ok=True)
    path=folder/("diagnostics-"+time.strftime("%Y%m%dT%H%M%S")+".json")
    write_json(path,report)
    return path


def backup(config):
    workspace=Path(config["setupWorkspace"])
    script=workspace/"create-development-backup.sh"
    if not script.is_file():
        raise OperationError("Choose your Windows setup workspace in Omachines settings")
    # Use the existing complete, reviewed backup provider, with hooks added by
    # this release for coherent locking and durable progress. Refuse an old provider.
    source=script.read_text()
    if "OMACHINES_PROGRESS_FD" not in source or "backup_copy_lock" not in source:
        raise OperationError("The backup provider needs the Omachines progress/locking update")
    # Preserve imported completion records before the provider advances its
    # latest-backup pointer. Never drop the original development baseline.
    write_json(state_dir()/"backups.json", backups(config))
    record("backup","running","Waiting for your Omarchy password")
    print("\nOmachines: complete Windows backup\n")
    print("Windows will shut down temporarily. The sudo prompt asks for your Omarchy password.")
    print("The backup includes the disk, TPM, firmware and private VM configuration. Shared files are separate.\n",flush=True)
    # stdout is tee'd to this visible terminal; retain only exact stage markers.
    p=subprocess.Popen(["sudo","/usr/bin/bash",str(script)],stdout=subprocess.PIPE,
                       stderr=None,stdin=None,text=True,bufsize=1,
                       env={**os.environ,"OMACHINES_PROGRESS_FD":"1"})
    # sudo may filter environment; provider emits stage markers unconditionally.
    stages={"preflight":"Checking Windows and backup space","stopping":"Shutting down Windows",
            "copying":"Copying the complete disk, TPM and firmware","verifying":"Verifying the archive and checksum",
            "starting":"Starting Windows again","complete":"Backup verified; Windows is starting"}
    for line in p.stdout:
        print(line,end="",flush=True)
        marker=line.strip()
        if marker.startswith("OMACHINES_STAGE:"):
            stage=marker.partition(":")[2]
            if stage in stages:
                record("backup","running",stages[stage])
    p.wait()
    if p.returncode:
        raise OperationError("Backup did not finish. Check this terminal; Windows may need starting.")
    try:
        path=Path((workspace/"research/development-ready.path").read_text().strip())
        status=(workspace/"research/development-ready.status").read_text().strip()
    except OSError:
        raise OperationError("Backup result could not be read") from None
    if status!="complete" or not path.is_dir() or not re.fullmatch(r"development-ready-\d{8}T\d{6}Z",path.name):
        raise OperationError("Backup completion could not be verified")
    records=read_json(state_dir()/"backups.json",[])
    records=records if isinstance(records,list) else []
    records=[r for r in records if isinstance(r,dict) and r.get("path")!=str(path)]
    records.append({"path":str(path),"label":"Windows backup","createdAt":now(),"verified":True,
                    "verificationSource":"Archive listing and SHA-256 checked during this backup","restoreTested":False})
    write_json(state_dir()/"backups.json",records)
    record("backup","running","Backup verified; checking Windows recovery")
    deadline=time.monotonic()+150
    while time.monotonic()<deadline:
        state=collect(config,deep=True)
        if state.get("checks",{}).get("ssh",{}).get("status")=="ready":
            cua=state["checks"].get("cua",{}).get("status")
            return "Backup verified. Windows is ready." if cua=="ready" else "Backup verified. Windows commands work; check desktop tools."
        time.sleep(4)
    return "Backup verified. Windows was started; its connections are not ready yet."


def perform(action, config):
    if action=="open":
        windows=viewer_windows()
        if windows:
            run(["hyprctl","dispatch","focuswindow","address:"+windows[0]],3)
            return "Windows viewer focused"
        launcher=wrapper("windows-vm-launch")
        if not launcher:
            raise OperationError("Windows viewer wrapper is missing")
        subprocess.Popen([launcher],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return "Windows viewer requested"
    if action=="start":
        vm_priv("up")
        return "Windows started; connections may take a moment"
    if action=="stop":
        vm_priv("down")
        return "Windows shut down"
    if action=="backup":
        return backup(config)
    if action=="console":
        run(["/usr/bin/omarchy-launch-browser","http://127.0.0.1:8006"],8)
        return "Recovery console requested"
    if action=="shared":
        path=Path.home()/"Windows"
        if not path.is_dir():
            raise OperationError("Shared folder does not exist")
        subprocess.Popen(["xdg-open",str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return "Shared folder opened"
    if action=="handoff":
        ssh=wrapper("windows-vm-ssh")
        if not ssh:
            raise OperationError("Windows SSH connection is missing")
        run([ssh,r"powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\OEM\guest-console.ps1"],20)
        return "Console handoff requested; check desktop status"
    if action=="aside":
        if not config["asideEnabled"]:
            raise OperationError("Aside integration is disabled")
        from core import guest_status,parse_guest
        guest=parse_guest(guest_status())
        if guest["desktop"]!="available":
            raise OperationError("Open and unlock the Windows desktop first")
        if not guest["asideInstalled"] or not guest["asidePath"] or not wrapper("windows-vm-mcp"):
            raise OperationError("Aside or Cua is not configured")
        with Client([wrapper("windows-vm-mcp")],15) as client:
            client.call("launch_app",{"path":guest["asidePath"]})
        return "Aside opened in Windows"
    if action=="diagnostics":
        path=write_diagnostics(config)
        subprocess.Popen(["xdg-open",str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return "Private diagnostic report saved and opened"
    raise OperationError("Unknown action")


def worker(action, interactive=False):
    config=settings()
    with open(state_dir()/"operation.lock","a") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return 1
        record(action,"running","Preparing "+action)
        try:
            message=perform(action,config)
            record(action,"complete",message)
            if action in TERMINAL or action=="diagnostics":
                notify(message,config)
            if interactive:
                print("\n"+message,flush=True)
            return 0
        except Exception as error:
            message=str(error) if isinstance(error,OperationError) else "Operation failed. Check the connection and try again."
            record(action,"failed",message)
            notify(message,config)
            if interactive:
                print("\n"+message,flush=True)
                try:
                    input("Press Enter to close this terminal. ")
                except EOFError:
                    pass
            return 1
