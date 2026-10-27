"""Explicit operations. A terminal-owned worker survives shell reloads."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

from core import (ROOT, OperationError, admission, backups, busy, check, collect, now, operation, read_json,
                  reservation_fresh, run, settings, state_dir, viewer_windows, windows_reachability, wrapper, write_json)
from mcp import Client
from probes import install_shutdown_handlers

ACTIONS = {"open", "start", "stop", "backup", "console", "shared", "aside", "handoff", "diagnostics", "inspect", "verify", "allocate", "rehearse", "rehearsal-pass", "rehearsal-fail", "rehearsal-image", "restore", "rollback", "label", "connections"}
ADMIN = {"inspect", "verify", "allocate", "rehearse", "rehearsal-pass", "rehearsal-fail", "restore", "rollback"}
TERMINAL = {"start", "stop", "backup"} | ADMIN
DISRUPTIVE = {"stop", "backup", "handoff", "allocate", "rehearse", "restore", "rollback", "rehearsal-pass", "rehearsal-fail"}
# The Omarchy VM needs no password: only restore uses a visible terminal, for
# its typed confirmation. Other operations are background workers.
OMARCHY_ACTIONS = {"open", "start", "stop", "shared", "backup", "verify", "label", "restore", "allocate", "diagnostics", "connections"}
OMARCHY_TERMINAL = {"restore"}
BACKUP_IDS = {"omarchy-windows": r"(?:baseline|development-ready|omachines)-\d{8}T\d{6}Z(?:-[0-9a-f]{6})?",
              "omarchy-qemu": r"omarchy-\d{8}T\d{6}Z"}
held = None      # this worker's locked operation.lock
OPEN_WAIT = 600  # seconds a Windows open may wait for authorization and startup
stopping = []    # termination signals deferred until the outcome is recorded


def omarchy(machine):
    return machine["provider"] == "omarchy-qemu"


def supported(action, machine):
    return action in (OMARCHY_ACTIONS if omarchy(machine) else ACTIONS)


def terminal(action, machine):
    return action in (OMARCHY_TERMINAL if omarchy(machine) else TERMINAL)


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


def say(text="", end="\n"):
    # The operation terminal can close mid-operation; the work continues.
    try:
        print(text, end=end, flush=True)
    except OSError:
        pass


@contextmanager
def provider(argv, detach=False, **kwargs):
    # A worker's provider runs under supervise.py, which inherits the locked
    # operation.lock and outlives a worker that dies early. The lock is thus
    # released only when the provider has finished. A detached provider runs
    # in its own session: the operation terminal's hangup, termination and
    # Ctrl+C reach only the worker. sudo needs that terminal for its password,
    # so Windows providers stay attached.
    if held is not None:
        argv = ["/usr/bin/python3", "-I", "-B", str(ROOT/"backend/supervise.py"), *argv]
        kwargs["pass_fds"] = (held.fileno(),)
    if detach:
        kwargs["start_new_session"] = True
    p = subprocess.Popen(argv, **kwargs)
    try:
        yield p
    finally:
        # Never report an outcome while the provider still runs. The
        # supervisor drains any output this worker no longer reads.
        if p.stdout:
            p.stdout.close()
        p.wait()


def notify(message, config):
    if config["notify"]:
        subprocess.Popen(["/usr/bin/notify-send","--app-name=Omachines","Omachines",message],
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def request_action(action, config, confirmed=False, parameters=None, machine_id=None):
    if action not in ACTIONS:
        raise OperationError("Unknown action")
    if config["demo"]:
        raise OperationError("Demo mode never controls a real VM")
    if action in DISRUPTIVE and not confirmed:
        raise OperationError("Confirm this action first")
    from machines import selected
    try: machine=selected()
    except ValueError:raise OperationError("Machine configuration needs attention") from None
    # A confirmation is for the machine it was shown with; never retarget it.
    if machine_id is not None and machine_id!=machine["id"]:
        raise OperationError("The selected machine changed; review it and try again")
    if not supported(action,machine):
        raise OperationError("This action is not available for "+machine["name"])
    parameters=validate_parameters(action,parameters or {},machine)
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
            if terminal(action,machine):
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
    with provider(["sudo","/usr/bin/env",f"PKEXEC_UID={os.getuid()}",
                   "/usr/bin/omarchy-windows-vm","__priv",action]) as result:
        pass
    if result.returncode:
        raise OperationError("VM operation failed or authentication was cancelled")


def open_windows(launcher):
    # The launcher can wait at an authorization prompt before it starts
    # Windows. Until its viewer appears this request owns the lifecycle and
    # keeps the lock, so a later shutdown or backup cannot be overtaken by an
    # old prompt approved afterwards. The viewer then runs on its own.
    argv,kwargs=[launcher],{"stdin":subprocess.DEVNULL,"stdout":subprocess.DEVNULL,"stderr":subprocess.DEVNULL,
                            "start_new_session":True}
    if held is not None:
        argv=["/usr/bin/python3","-I","-B",str(ROOT/"backend/supervise.py"),"--until-released",launcher]
        kwargs["pass_fds"]=(held.fileno(),)
    record("open","running","Starting Windows; approve the authorization prompt if one appears")
    p=subprocess.Popen(argv,**kwargs)
    deadline=time.monotonic()+OPEN_WAIT
    while p.poll() is None:
        if viewer_windows():
            if held is not None:
                p.send_signal(signal.SIGUSR1);p.wait()
            return "Windows viewer opened"
        if time.monotonic()>deadline:
            # Withdraw the request, including a prompt nobody answered.
            try:os.killpg(p.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            p.wait()
            raise OperationError("Windows did not start within 10 minutes; the request was withdrawn")
        time.sleep(0.5)
    if p.returncode:
        raise OperationError("Windows did not start, or authorization was cancelled")
    return "Windows viewer closed"


def ensure_memory():
    # Refuse before starting Windows when the host has less memory free than
    # its configured RAM (8 GiB when unknown). Not needed once it is running.
    if windows_reachability({})[0] in ("running","starting"):
        return
    import machines
    allocation=machines.admin_state().get("allocation")
    ram=re.fullmatch(r"([1-9][0-9]{0,3})G",str(allocation.get("ram","") if isinstance(allocation,dict) else ""))
    need=int(ram[1]) if ram else 8
    free=machines.host_resources()["availableBytes"]
    if free and free<need*1024**3:
        raise OperationError(f"Only {int(free*10/1024**3)/10:g} GiB of memory is free; Windows needs {need} GiB. "
                             "Shut down the other VM or close apps first.")


def write_diagnostics(config, machine_id=None):
    state=collect(config,deep=True,machine_id=machine_id)
    # Strict output allowlist: no guest paths, IPs, account IDs, windows or raw logs.
    from core import VERSION
    report={"version":VERSION,"machineId":state.get("machineId"),"provider":state.get("provider"),"checkedAt":state["checkedAt"],"state":state["state"],
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
    say("\nOmachines: complete Windows backup\n")
    say("Windows will shut down temporarily. The sudo prompt asks for your Omarchy password.")
    say("The backup includes the disk, TPM, firmware and private VM configuration. Shared files are separate.\n")
    # sudo may filter environment; provider emits stage markers unconditionally.
    stages={"preflight":"Checking Windows and backup space","stopping":"Shutting down Windows",
            "copying":"Copying the complete disk, TPM and firmware","verifying":"Verifying the archive and checksum",
            "starting":"Starting Windows again","complete":"Backup verified; Windows is starting"}
    # stdout is tee'd to this visible terminal; retain only exact stage markers.
    with provider(["sudo","/usr/bin/bash",str(script)],stdout=subprocess.PIPE,
                  stderr=None,stdin=None,text=True,bufsize=1,
                  env={**os.environ,"OMACHINES_PROGRESS_FD":"1"}) as p:
        for line in p.stdout:
            say(line,end="")
            marker=line.strip()
            if marker.startswith("OMACHINES_STAGE:"):
                stage=marker.partition(":")[2]
                if stage in stages:
                    record("backup","running",stages[stage])
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
    while time.monotonic()<deadline and not stopping:
        # Pinned: the panel may switch to another machine during the backup.
        state=collect(config,deep=True,machine_id="windows")
        if state.get("checks",{}).get("ssh",{}).get("status")=="ready":
            cua=state["checks"].get("cua",{}).get("status")
            return "Backup verified. Windows is ready." if cua=="ready" else "Backup verified. Windows commands work; check desktop tools."
        time.sleep(4)
    return "Backup verified. Windows was started; its connections are not ready yet."


def windows_anchor():
    return Path("/var/lib/omarchy/windows/mounts/users")/str(os.getuid())/"storage"


def windows_storage():
    from machines import windows_machine
    return Path(windows_machine()["storagePath"])


def mount_points():
    # Field 5 of mountinfo is the mount point, with octal escapes for spaces.
    # os.path.ismount compares devices, so it misses bind mounts within one
    # filesystem; the kernel's mount table does not.
    try:
        with open("/proc/self/mountinfo") as f:
            return {re.sub(r"\\([0-7]{3})",lambda m:chr(int(m.group(1),8)),fields[4])
                    for fields in (line.split() for line in f) if len(fields)>4}
    except OSError:
        return set()


def windows_storage_attached(anchor):
    # Mounted, and the mount is this user's Windows storage.
    if str(anchor) not in mount_points():
        return False
    try:
        return os.path.samefile(windows_storage(),anchor)
    except OSError:
        return False


def wait_windows_ready(timeout=300):
    from core import guest_status
    if not wrapper("windows-vm-ssh"):
        say("Windows SSH is not configured; allowing two minutes for Windows to finish starting.")
        time.sleep(120)
        return True
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline and not stopping:
        try:
            guest_status()
            return True
        except (OperationError,ValueError,OSError):
            time.sleep(5)
    return False


def attach_windows_storage():
    # After a host restart, Omarchy recreates its protected storage bind only
    # in `__priv up`. The root helper requires that bind, so attach it with the
    # existing lifecycle command. `__priv down` stops the container and keeps
    # the bind, so the helper then backs up a cleanly shut down Windows.
    anchor=windows_anchor()
    # Anything already mounted there takes the normal path; the helper checks
    # its identity and refuses a wrong one.
    if not anchor.parent.is_dir() or str(anchor) in mount_points() or windows_reachability({})[0] in ("running","starting"):
        return False
    ensure_memory()
    say("\nWindows storage is not attached: Omarchy attaches it when Windows starts, and the host has")
    say("restarted since then. Omachines will start Windows, wait for it to finish starting, shut it")
    say("down cleanly, then back it up. Windows stays stopped afterwards. sudo may ask for your")
    say("Omarchy password more than once.\n")
    record("backup","running","Starting Windows to attach its storage")
    vm_priv("up")
    error=None
    try:
        if not windows_storage_attached(anchor):
            raise OperationError("Windows storage is still not attached; nothing was backed up")
        record("backup","running","Waiting for Windows to finish starting")
        if not wait_windows_ready():
            raise OperationError("Windows did not finish starting; it was shut down again and nothing was backed up")
    except OperationError as failure:
        error=failure
    record("backup","running","Shutting down Windows before the backup")
    say("Shutting down Windows before the backup...")
    vm_priv("down")
    if error:
        raise error
    return True


def perform(action, config, parameters=None, machine=None):
    if machine is None:
        from machines import selected
        machine=selected()
    if not supported(action,machine):
        raise OperationError("This action is not available for "+machine["name"])
    parameters=validate_parameters(action,parameters or {},machine)
    if action=="diagnostics":
        path=write_diagnostics(config,machine["id"])
        subprocess.Popen(["xdg-open",str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return "Private diagnostic report saved and opened"
    if omarchy(machine):
        import omarchy as provider
        return provider.perform(action,config,parameters,machine)
    if action in ADMIN:return admin_action(action,parameters)
    if action=="open":
        windows=viewer_windows()
        if windows:
            run(["hyprctl","dispatch",'hl.dsp.focus({ window = "address:'+windows[0]+'" })'],3)
            return "Windows viewer focused"
        launcher=wrapper("windows-vm-launch")
        if not launcher:
            raise OperationError("Windows viewer wrapper is missing")
        ensure_memory()
        return open_windows(launcher)
    if action=="start":
        ensure_memory()
        vm_priv("up")
        return "Windows started; connections may take a moment"
    if action=="stop":
        vm_priv("down")
        return "Windows shut down"
    if action=="backup":
        if Path("/usr/local/lib/omachines/admin.py").is_file():
            attached=attach_windows_storage()
            message=admin_action(action,parameters)
            return message+("; Windows was left stopped, as before the backup" if attached else "")
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
        m=machine;folder=state_dir()/'reports';folder.mkdir(exist_ok=True,mode=0o700)
        path=folder/'connections.txt'
        text='Omachines connection guide\n\nWindows commands: '+m['connections']['ssh']+'\nWindows GUI MCP: '+m['connections']['cua']+'\nAside browser MCP: '+m['connections']['aside']+'\n\nConfigure these existing commands in your chosen agent; no passwords belong in MCP settings.\nOpen and unlock Windows for GUI work. Open Aside for browser tools.\nAside CLI sign-in and model access are separate from browser availability.\nRemote debugging is not automatically enabled by Omachines.\nAgents can reach accounts signed into this Windows user.\n'
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:f.write(text)
        subprocess.Popen(['xdg-open',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        return 'Private connection instructions opened'
    raise OperationError("Unknown action")


def defer(signum, _frame):
    # A worker asked to stop keeps supervising its provider and records the
    # outcome, then exits; it never abandons work that is still running.
    stopping.append(signum)
    if signum==signal.SIGHUP:
        # The operation terminal closed; continue without it.
        null=os.open(os.devnull,os.O_RDWR)
        for fd in (0,1,2):os.dup2(null,fd)
        os.close(null)


def worker(action, request_id, interactive=False):
    stopping.clear()
    previous={}
    if threading.current_thread() is threading.main_thread():
        # Supersede the probe owner's handlers, which exit at once.
        install_shutdown_handlers()
        previous={sig:signal.signal(sig,defer) for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT)}
    try:
        return supervised(action,request_id,interactive)
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)


def supervised(action, request_id, interactive):
    global held
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
                from machines import get
                try:
                    # Pinned to the machine it was requested for, even if the
                    # panel switched machines while the worker was starting.
                    machine=get(reservation.get('machineId','windows'))
                    if not supported(action,machine):
                        raise ValueError('Unsupported action')
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
                if stopping:
                    record(action,"failed","Stopped before it began; nothing was changed")
                    raise OperationError("Stopped before it began; nothing was changed")
                held=lock
                record(action,"running","Preparing "+action,requestId=request_id)
        except OperationError as error:
            if interactive:
                say(str(error))
            return 1
        try:
            message=perform(action,config,reservation.get("parameters",{}),machine)
            record(action,"complete",message)
            if action in TERMINAL or action in ("diagnostics","backup","verify","allocate"):
                notify(message,config)
            if interactive:
                say("\n"+message)
            code=0
        except Exception as error:
            message=str(error) if isinstance(error,OperationError) else "Operation failed. Check the connection and try again."
            record(action,"failed",message)
            notify(message,config)
            if interactive and not stopping:
                say("\n"+message)
                try:
                    input("Press Enter to close this terminal. ")
                except (EOFError,OSError):
                    pass
            code=1
        finally:
            held=None
        return 128+stopping[0] if stopping else code


def validate_parameters(action, values, machine=None):
    if not isinstance(values,dict):raise OperationError('Invalid action parameters')
    result={}
    if action in ('verify','rehearse','restore','label'):
        name=values.get('backupId','')
        pattern=BACKUP_IDS[machine["provider"] if machine else "omarchy-windows"]
        if not isinstance(name,str) or not re.fullmatch(pattern,name):raise OperationError('Select a backup first')
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
    messages=[]
    with provider(args,stdout=subprocess.PIPE,text=True,bufsize=1) as p:
        for line in p.stdout:
            say(line,end='')
            line=line.strip()
            if line.startswith('OMACHINES_STAGE:'):
                stage=line.partition(':')[2]
                record(action,'running',{'preflight':'Checking configuration and capacity','stopping':'Stopping Windows','copying':'Copying Windows storage','verifying':'Checking backup integrity','restoring':'Restoring into a separate directory','booting':'Booting the offline restored copy','starting':'Starting Windows','complete':'Operation finishing'}.get(stage,'Operation in progress'))
            elif line:messages.append(line[:300]);messages=messages[-3:]
    if p.returncode:raise OperationError(messages[-1] if messages else 'Administrator operation failed or authentication was cancelled')
    return messages[-1] if messages else 'Administrator operation complete'
