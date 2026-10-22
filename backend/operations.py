"""Explicit operations. A terminal-owned worker survives shell reloads."""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from core import (ROOT, OperationError, admission, backups, busy, check, collect, now, operation, read_json,
                  reservation_fresh, run, settings, state_dir, viewer_windows, wrapper, write_json)
from mcp import Client

ACTIONS = {"open", "start", "stop", "backup", "console", "shared", "aside", "handoff", "diagnostics", "inspect", "verify", "allocate", "rehearse", "rehearsal-pass", "rehearsal-fail", "rehearsal-image", "restore", "rollback", "label", "connections"}
ADMIN = {"inspect", "verify", "allocate", "rehearse", "rehearsal-pass", "rehearsal-fail", "restore", "rollback"}
TERMINAL = {"start", "stop", "backup"} | ADMIN
DISRUPTIVE = {"stop", "backup", "handoff", "allocate", "rehearse", "restore", "rollback", "rehearsal-pass", "rehearsal-fail"}


def record(action, status, message, **extra):
    value={"action":action,"status":status,"message":message,"updatedAt":now(),"pid":os.getpid(),**extra}
    old=read_json(state_dir()/"operation.json",{})
    if old.get('action')==action and old.get('status') in ('starting','running'):
        for key in ('requestId','machineId','parameters'):
            if key in old and key not in value:value[key]=old[key]
    value.setdefault('machineId','windows')
    write_json(state_dir()/"operation.json",value)
    if status in ('complete','failed'):
        import history
        history.append(value)
    return value


def notify(message, config):
    if config["notify"]:
        subprocess.Popen(["/usr/bin/notify-send","--app-name=Omachines","Omachines",message],
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def request_action(action, config, confirmed=False, parameters=None):
    if action not in ACTIONS:
        raise OperationError("Unknown action")
    if config["demo"]:
        raise OperationError("Demo mode never controls a real VM")
    if action in DISRUPTIVE and not confirmed:
        raise OperationError("Confirm this action first")
    from machines import selected
    try: machine=selected()
    except ValueError:raise OperationError("Machine configuration needs attention") from None
    parameters=validate_parameters(action,parameters or {})
    # Reservation prevents two widgets from starting overlapping workers.
    with admission():
        if busy():
            raise OperationError("Another operation is still running")
        previous=operation()
        if previous.get('status')=='interrupted':
            import history
            history.append(previous)
        request_id = uuid.uuid4().hex
        started = time.time()
        argv=["/usr/bin/python3","-B",str(ROOT/"backend/omachinesctl"),"worker",action,
              "--request-id",request_id]
        record(action,"starting","Waiting for the operation to start",startedEpoch=started,
               expiresEpoch=started+120,requestId=request_id,machineId=machine["id"],parameters=parameters)
        try:
            if action in TERMINAL:
                subprocess.Popen(["/usr/bin/omarchy-launch-terminal",*argv,"--interactive"],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            else:
                subprocess.Popen(argv,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        except OSError:
            record(action,"failed","Could not start the operation")
            raise OperationError("Could not open the operation terminal") from None
        return request_id


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
    from core import VERSION
    report={"version":VERSION,"checkedAt":state["checkedAt"],"state":state["state"],
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
    try:
        old_path = (workspace/"research/development-ready.path").read_text().strip()
    except FileNotFoundError:
        old_path = ""
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
    if (status!="complete" or not path.is_absolute() or str(path)==old_path
            or not path.is_dir() or not re.fullmatch(r"development-ready-\d{8}T\d{6}Z",path.name)):
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


def perform(action, config, parameters=None):
    parameters=validate_parameters(action,parameters or {})
    if action in ADMIN:return admin_action(action,parameters)
    if action=="open":
        windows=viewer_windows()
        if windows:
            run(["hyprctl","dispatch",'hl.dsp.focus({ window = "address:'+windows[0]+'" })'],3)
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
        if Path("/usr/local/lib/omachines/admin.py").is_file():return admin_action(action,parameters)
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
    if action=="label":
        rows=backups(config)
        target=next((r for r in rows if Path(r.get('path','')).name==parameters['backupId']),None)
        if target is None:
            from machines import admin_state
            target=next((r for r in admin_state().get('backups',[]) if r.get('id')==parameters['backupId']),None)
        if target is None:raise OperationError('Select a backup before naming it')
        target={**target,'label':parameters['label']}
        rows=[r for r in rows if r.get('path')!=target['path']]+[target]
        write_json(state_dir()/'backups.json',rows)
        return 'Backup name saved'
    if action=="rehearsal-image":
        from machines import admin_state
        path=admin_state().get('rehearsal',{}).get('screenshot','')
        if path!=f'/var/lib/omachines/users/{os.getuid()}/rehearsal.ppm' or not Path(path).is_file():raise OperationError('No rehearsal image is available')
        subprocess.Popen(['xdg-open',path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return 'Rehearsal image opened; review the Windows boot before recording success'
    if action=="connections":
        from machines import selected
        m=selected();folder=state_dir()/'reports';folder.mkdir(exist_ok=True,mode=0o700)
        path=folder/'connections.txt'
        text='Omachines connection guide\n\nWindows commands: '+m['connections']['ssh']+'\nWindows GUI MCP: '+m['connections']['cua']+'\nAside browser MCP: '+m['connections']['aside']+'\n\nConfigure these existing commands in your chosen agent; no passwords belong in MCP settings.\nOpen and unlock Windows for GUI work. Open Aside for browser tools.\nAside CLI sign-in and model access are separate from browser availability.\nRemote debugging is not automatically enabled by Omachines.\nAgents can reach accounts signed into this Windows user.\n'
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:f.write(text)
        subprocess.Popen(['xdg-open',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return 'Private connection instructions opened'
    if action=="diagnostics":
        path=write_diagnostics(config)
        subprocess.Popen(["xdg-open",str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return "Private diagnostic report saved and opened"
    raise OperationError("Unknown action")


def worker(action, request_id, interactive=False):
    with open(state_dir()/"operation.lock","a") as lock:
        try:
            # The child can start before Popen returns in the requesting
            # process. Allow that short admission-lock handoff, then validate
            # identity and expiry while holding the gate.
            with admission(timeout=2):
                reservation = read_json(state_dir()/"operation.json", {})
                if (not isinstance(reservation, dict) or not request_id
                        or reservation.get("requestId") != request_id
                        or reservation.get("action") != action
                        or reservation.get("status") != "starting"
                        or not reservation_fresh(reservation)):
                    raise OperationError("This operation request expired or was superseded; request it again")
                config=settings()
                from machines import selected
                try:
                    if selected()['id']!=reservation.get('machineId','windows'):
                        raise ValueError('Machine changed')
                except ValueError:
                    record(action,'failed','Machine configuration changed; request the action again')
                    raise OperationError('Machine configuration changed; request the action again') from None
                if config["demo"]:
                    record(action,"failed","Demo mode never controls a real VM")
                    raise OperationError("Demo mode never controls a real VM")
                try:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:
                    raise OperationError("Another operation is still running") from None
                record(action,"running","Preparing "+action,requestId=request_id)
        except OperationError as error:
            if interactive:
                print(str(error),flush=True)
            return 1
        try:
            message=perform(action,config,reservation.get("parameters",{}))
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


def validate_parameters(action, values):
    if not isinstance(values,dict):raise OperationError('Invalid action parameters')
    result={}
    if action in ('verify','rehearse','restore','label'):
        name=values.get('backupId','')
        if not isinstance(name,str) or not re.fullmatch(r'(?:baseline|development-ready|omachines)-\d{8}T\d{6}Z(?:-[0-9a-f]{6})?',name):raise OperationError('Select a backup first')
        result['backupId']=name
    if action=='label':
        label=values.get('label','')
        if not isinstance(label,str) or not 1<=len(label.strip())<=60 or any(ord(c)<32 for c in label):raise OperationError('Use a name of 1–60 characters')
        result['label']=label.strip()
    if action=='allocate':
        for key in ('cpu','ram'):
            if type(values.get(key))!=int or not 1<=values[key]<=256:raise OperationError('Enter valid CPU and RAM allocations')
            result[key]=values[key]
    if set(values)-set(result):raise OperationError('Unexpected action parameters')
    return result


def admin_action(action,parameters):
    helper=Path('/usr/local/lib/omachines/admin.py')
    if not helper.is_file() or helper.is_symlink() or helper.stat().st_uid!=0 or helper.stat().st_mode&0o022:raise OperationError('Install the Omachines administrator helper first')
    args=['sudo','/usr/bin/python3','-I',str(helper),action]
    for key,flag in (('backupId','--backup-id'),('cpu','--cpu'),('ram','--ram')):
        if key in parameters:args += [flag,str(parameters[key])]
    # Keep stdin attached to the visible operation terminal for sudo and restore
    # confirmation. Output contains only administrator-helper stage summaries.
    p=subprocess.Popen(args,stdout=subprocess.PIPE,text=True,bufsize=1)
    messages=[]
    for line in p.stdout:
        print(line,end='',flush=True)
        line=line.strip()
        if line.startswith('OMACHINES_STAGE:'):
            stage=line.partition(':')[2]
            record(action,'running',{'preflight':'Checking configuration and capacity','stopping':'Stopping Windows','copying':'Copying Windows storage','verifying':'Checking backup integrity','restoring':'Restoring into a separate directory','booting':'Booting the offline restored copy','starting':'Starting Windows','complete':'Operation finishing'}.get(stage,'Operation in progress'))
        elif line:messages.append(line[:300]);messages=messages[-3:]
    p.wait()
    if p.returncode:raise OperationError(messages[-1] if messages else 'Administrator operation failed or authentication was cancelled')
    return messages[-1] if messages else 'Administrator operation complete'
