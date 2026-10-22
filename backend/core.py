"""Omarchy Windows adapter and private state. Standard library only."""
import base64
from contextlib import contextmanager
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import socket
import time

from mcp import Client, ProbeError
from probes import output

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0"
ID = "atlastimed.omachines"


class OperationError(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise OperationError("State directory must be owned by your user and not a symlink")
    path.chmod(0o700)
    return path


def state_dir():
    return private_dir(Path(os.environ.get("XDG_STATE_HOME", Path.home()/".local/state"))/"omachines")


def read_json(path, default=None):
    try:
        if Path(path).stat().st_size > 2 * 1024 * 1024:
            return default
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, UnicodeError):
        return default


def write_json(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def flag(value, default=False):
    if value is None:
        return default
    if isinstance(value, str):
        return value.lower().strip() not in ("false", "0", "off", "no", "")
    return bool(value)


def settings(overrides=None):
    shell = read_json(Path.home()/".config/omarchy/shell.json", {})
    entry = {}
    for section in shell.get("bar", {}).get("layout", {}).values():
        if isinstance(section, list):
            for item in section:
                if isinstance(item, dict) and item.get("id") == ID:
                    entry.update(item)
    entry.update(overrides or {})
    try:
        interval = max(30, min(900, int(entry.get("pollSeconds", 120))))
    except (ValueError, TypeError):
        interval = 120
    workspace = str(entry.get("setupWorkspace", "")).strip()
    return {"pollSeconds": interval, "notify": flag(entry.get("notify"), True),
            "asideEnabled": flag(entry.get("asideEnabled"), True),
            "setupWorkspace": str(Path(workspace).expanduser()) if workspace else "",
            "demo": flag(entry.get("demo")), "showLabel": flag(entry.get("showLabel"), True)}


def run(command, timeout=15):
    # All callers supply argv; remote output is parsed, never included in errors.
    try:
        return output(command, timeout)
    except ProbeError as error:
        raise OperationError(str(error)) from None


def wrapper(name):
    path = Path.home()/".local/bin"/name
    return str(path) if path.is_file() and os.access(path, os.X_OK) else None


def tcp(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def check(code, message, stamp=None):
    return {"status": code, "message": message, "checkedAt": stamp or now()}


def guest_status():
    ssh = wrapper("windows-vm-ssh")
    if not ssh:
        raise OperationError("Windows SSH wrapper is not configured")
    code = (ROOT/"backend/guest-status.ps1").read_text()
    encoded = base64.b64encode(code.encode("utf-16-le")).decode()
    return json.loads(run([ssh, f"powershell.exe -NoProfile -NonInteractive -EncodedCommand {encoded}"], 18))


def parse_guest(raw):
    if not isinstance(raw, dict):
        raise ValueError("Invalid guest result")
    desktop = raw.get("desktop", "unknown")
    if desktop not in ("available", "locked", "disconnected", "signed-out", "unknown"):
        desktop = "unknown"
    result = {"desktop": desktop}
    for key in ("cpu", "memoryBytes", "diskBytes", "diskFreeBytes"):
        value = raw.get(key)
        result[key] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    result["asideInstalled"] = raw.get("asideInstalled") is True
    result["asideRunning"] = raw.get("asideRunning") is True
    path = raw.get("asidePath", "")
    result["asidePath"] = path if isinstance(path, str) and re.fullmatch(r"[A-Za-z]:\\Users\\[^\\\r\n]+\\AppData\\Local\\Aside\\Application\\aside\.exe", path, re.I) else ""
    result["boot"] = raw.get("boot", "")[:40] if isinstance(raw.get("boot"), str) else ""
    return result


def viewer_windows():
    if not shutil.which("hyprctl"):
        return []
    try:
        clients = json.loads(run(["hyprctl", "clients", "-j"], 3))
        return [c["address"] for c in clients if c.get("class") in ("xfreerdp", "wlfreerdp")
                and c.get("title") == "Windows VM - Omarchy" and re.fullmatch(r"0x[0-9a-f]+", c.get("address", ""))]
    except (OperationError, ValueError, OSError):
        return []


def reservation_fresh(value):
    started = value.get("startedEpoch")
    expires = value.get("expiresEpoch")
    return (isinstance(started, (float, int)) and isinstance(expires, (float, int))
            and 0 <= time.time() - started < 120 and time.time() < expires)


@contextmanager
def admission(timeout=0):
    # Lock order: request gate, then operation lock. Maintenance keeps both
    # for its entire transaction. Workers release the gate after consumption.
    with open(state_dir()/"request.lock", "a") as lock:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise OperationError("Omachines maintenance or another request is in progress") from None
                time.sleep(0.02)
        yield


@contextmanager
def maintenance():
    with admission():
        if busy():
            raise OperationError("Wait for the Omachines operation to finish before updating or removing it")
        with open(state_dir()/"operation.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OperationError("An Omachines operation is still running") from None
            yield


def operation():
    value = read_json(state_dir()/"operation.json", {})
    if not isinstance(value, dict):
        return {}
    if value.get("status") in ("running", "starting"):
        pid = value.get("pid", 0)
        alive = isinstance(pid, int) and pid > 0 and Path(f"/proc/{pid}").exists()
        if value.get("status") == "starting":
            alive = reservation_fresh(value)
        else:
            alive = alive and operation_locked()
        if not alive:
            value = {**value, "status": "interrupted", "message": "Operation interrupted. Check Windows and the backup terminal before retrying."}
    return value


def operation_locked():
    with open(state_dir()/"operation.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def busy():
    if operation_locked():
        return True
    return operation().get("status") in ("running", "starting")


def fresh_checks(previous, interval):
    try:
        stamp = previous["checks"]["ssh"]["checkedAt"]
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(stamp)).total_seconds()
        return 0 <= age <= max(60, interval * 2)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def refresh_snapshot(config, previous, deep):
    try:
        return collect(config, deep=deep, previous=previous)
    except Exception:
        # A failed collection must not republish old readiness. Shallow polls
        # retain this failure until a deep probe produces replacement evidence.
        return {**previous, "state":"unknown", "summary":"Connection checks failed",
                "error":"Connection check failed; check the connections and retry",
                "checks":{key:check("error", "Connection state unavailable")
                          for key in ("ssh", "desktop", "cua", "aside")},
                "resources":{}, "guest":{}}


def backups(config):
    # Summaries are separate from root-only backup contents. Merely finding a
    # directory never establishes verification. Imported records state their source.
    records = read_json(state_dir()/"backups.json", [])
    if not isinstance(records, list):
        records = []
    found = {r.get("path"): r for r in records if isinstance(r, dict)}
    workspace = Path(config["setupWorkspace"]) if config["setupWorkspace"] else None
    if workspace:
        validation = read_json(workspace/"research/development-validation.json", {}) or {}
        evidence = validation.get("backup", {})
        # The immutable validation report still proves an older baseline when
        # the mutable latest-backup status is changed by a subsequent backup.
        for field in ("originalBaseline", "developmentReady"):
            previous_path = evidence.get(field)
            previous = found.get(previous_path)
            if previous and evidence.get("completed") is True and evidence.get("sha256Verification") == "passed":
                found[previous_path] = {**previous, "verified": True,
                    "verificationSource": "Existing setup completion record"}
        for key, label, field in (("baseline", "Initial baseline", "originalBaseline"), ("development-ready", "Development-ready", "developmentReady")):
            try:
                path = (workspace/f"research/{key}.path").read_text().strip()
                status = (workspace/f"research/{key}.status").read_text().strip()
            except OSError:
                continue
            target = Path(path)
            if not target.is_absolute() or not target.is_dir():
                continue
            verified = status == "complete" and evidence.get(field) == path
            if key == "development-ready":
                verified = verified and evidence.get("sha256Verification") == "passed"
            found.setdefault(path, {"path": path, "label": label, "verified": verified,
                "verificationSource": "Existing setup completion record" if verified else "Not checked by Omachines",
                "createdAt": dt.datetime.fromtimestamp(target.stat().st_mtime, dt.timezone.utc).isoformat(),
                "restoreTested": False})
    return sorted(found.values(), key=lambda r: r.get("createdAt", ""), reverse=True)[:20]


def capabilities(config):
    workspace = Path(config["setupWorkspace"]) if config["setupWorkspace"] else None
    return {"viewer": bool(wrapper("windows-vm-launch")), "ssh": bool(wrapper("windows-vm-ssh")),
            "cua": bool(wrapper("windows-vm-mcp")), "aside": bool(wrapper("windows-vm-aside-mcp")) and config["asideEnabled"],
            "backup": bool(workspace and (workspace/"create-development-backup.sh").is_file()),
            "omarchy": Path("/usr/bin/omarchy-windows-vm").is_file()}


def sample_state():
    return {"schemaVersion":1,"name":"Windows 11","state":"running","summary":"Windows is ready","checkedAt":now(),
            "checks":{"ssh":check("ready","Command access ready"),"desktop":check("available","Desktop available"),
                      "cua":check("ready","Desktop tools responding"),"aside":check("closed","Open Aside to use its browser tools")},
            "resources":{"cpu":4,"memoryBytes":8589934592,"diskBytes":204946587648,"diskFreeBytes":149485748224},
            "capabilities":{"viewer":True,"ssh":True,"cua":True,"aside":True,"backup":True,"omarchy":True},
            "backups":[{"label":"Development-ready","createdAt":now(),"verified":True,"restoreTested":False}],
            "operation":{},"demo":True,"viewerOpen":False,"storage":{"availableBytes":107374182400},"sharedPath":"~/Windows"}


def collect_windows(config, deep=False, previous=None):
    if config["demo"]:
        return sample_state()
    caps = capabilities(config)
    op = operation()
    s = {"schemaVersion":1,"name":"Windows 11","checkedAt":now(),"demo":False,"capabilities":caps,
         "operation":op,"viewerOpen":bool(viewer_windows()),"backups":backups(config),"checks":{},
         "sharedPath":str(Path.home()/"Windows"),"storage":{},"resources":{},"error":""}
    try:
        usage = shutil.disk_usage(Path.home()/".windows")
        s["storage"] = {"availableBytes":usage.free,"totalBytes":usage.total}
    except OSError:
        pass
    ssh_port, console = tcp(2222), tcp(8006)
    if not ssh_port and not console:
        s.update(state="unreachable", summary="Windows is not reachable")
        backup_active = op.get("action") == "backup" and op.get("status") in ("starting", "running")
        if backup_active:
            s["summary"] = "Backup in progress"
        for name in ("ssh","desktop","cua","aside"):
            s["checks"][name]=check("offline","Unavailable during backup" if backup_active else "Start Windows, or check the recovery console")
        if op.get("action")=="stop" and op.get("status")=="complete":
            s.update(state="stopped",summary="Windows is stopped")
        return s
    s.update(state="starting" if not ssh_port else "running", summary="Windows is starting" if not ssh_port else "Windows is reachable")
    if not deep:
        if previous:
            s["error"] = previous.get("error", "")
            s["checks"] = dict(previous.get("checks", {}))
            s["resources"] = previous.get("resources", {})
            s["guest"] = previous.get("guest", {})
            if s["error"]:
                s["summary"]="Windows is reachable; connection checks failed"
            elif ssh_port and previous.get("state")=="running" and fresh_checks(previous, config["pollSeconds"]):
                s["summary"]=previous.get("summary",s["summary"])
            elif ssh_port:
                s["summary"]="Windows is reachable; connection checks are due"
        return s
    s["checks"]["ssh"]=check("missing","SSH connection is not configured")
    if not caps["ssh"] or not ssh_port:
        return s
    try:
        guest = parse_guest(guest_status())
        s["guest"] = guest
        s["resources"] = {k:guest[k] for k in ("cpu","memoryBytes","diskBytes","diskFreeBytes")}
        s["checks"]["ssh"]=check("ready","Command access ready")
        messages={"available":"Desktop available","locked":"Unlock Windows to resume GUI work",
                  "disconnected":"Reconnect the desktop or return it to the console","signed-out":"Sign in to Windows","unknown":"Desktop state could not be determined"}
        s["checks"]["desktop"]=check(guest["desktop"],messages[guest["desktop"]])
        s["summary"] = "Windows is ready" if guest["desktop"]=="available" else messages[guest["desktop"]]
    except (OperationError, ValueError, OSError):
        s["checks"]["ssh"]=check("error","SSH did not respond. Check the connection or recovery console.")
        s["checks"]["desktop"]=check("unknown","Desktop state unavailable")
        return s
    s["checks"]["cua"]=check("missing","Cua connection is not configured")
    if caps["cua"]:
        try:
            with Client([wrapper("windows-vm-mcp")],10) as client:
                result=client.call("health_report",{"include":["session_active","ax_capability","screen_capture_capability"]})
                if not isinstance(result, dict):
                    raise ProbeError("Invalid Cua result")
                health=result.get("structuredContent",{})
                if not isinstance(health, dict):
                    raise ProbeError("Invalid Cua health response")
                ok=health.get("schema_version")=="1" and health.get("overall")=="ok"
                s["checks"]["cua"]=check("ready" if ok else "error","Desktop tools responding" if ok else "Cua needs attention")
        except (OSError, ProbeError, BrokenPipeError):
            s["checks"]["cua"]=check("error","Cua did not respond. Unlock Windows and check again.")
    s["checks"]["aside"]=check("disabled","Aside integration is disabled")
    if caps["aside"]:
        s["checks"]["aside"]=check("closed","Open Aside to use its browser tools")
        if guest["asideRunning"]:
            try:
                with Client([wrapper("windows-vm-aside-mcp")],10) as client:
                    client.call("repl",{"title":"Omachines connection check","code":"console.log('Omachines browser connection ready')"})
                s["checks"]["aside"]=check("ready","Browser tools responding; model sign-in not checked")
            except (OSError, ProbeError, BrokenPipeError):
                s["checks"]["aside"]=check("error","Browser is open; its tools did not respond")
    return s


def collect(config, deep=False, previous=None):
    import machines
    import history
    if config.get("demo"):
        state=sample_state()
        state.update(host={"cpu":16,"memoryBytes":32*1024**3,"availableBytes":16*1024**3},history=[],machineId="windows")
        return state
    try:
        machine=machines.selected()
    except ValueError:
        return {"state":"unknown","summary":"Machine configuration needs attention","error":"Unsupported machine registry; no connections attempted","checks":{},"capabilities":{},"resources":{},"operation":operation(),"backups":[]}
    state=collect_windows(config,deep,previous)
    admin=machines.admin_state()
    state.update(name=machine['name'],machineId=machine['id'],machines=[{"id":machine['id'],"name":machine['name'],"provider":machine['provider']}],host=machines.host_resources(),allocation=admin.get('allocation',{}),rehearsal=admin.get('rehearsal',{}),recovery=admin.get('recovery',{}),history=list(reversed(history.entries()))[:20])
    helper=Path('/usr/local/lib/omachines/admin.py')
    ready=helper.is_file() and not helper.is_symlink() and helper.stat().st_uid==0 and not helper.stat().st_mode&0o022
    state['capabilities'].update(admin=ready,backup=ready or state['capabilities'].get('backup',False))
    # Preserve user labels while administrative evidence supersedes imported status.
    found={Path(r['path']).name:r for r in state['backups'] if r.get('path')}
    for r in admin.get('backups',[]):
        if not isinstance(r,dict) or not isinstance(r.get('id'),str):continue
        old=found.get(r['id'],{})
        found[r['id']]={**old,**r,'label':old.get('label','Windows backup')}
    state['backups']=sorted(found.values(),key=lambda r:r.get('createdAt',''),reverse=True)[:30]
    if deep and state['checks'].get('ssh',{}).get('status')=='ready' and config['asideEnabled']:
        try:
            code=(ROOT/'backend/aside-cli.ps1').read_text()
            # Invoke the script block with its default status mode.
            encoded=base64.b64encode(code.encode('utf-16-le')).decode()
            result=json.loads(run([wrapper('windows-vm-ssh'),f'powershell.exe -NoProfile -NonInteractive -EncodedCommand {encoded}'],15))
            if not isinstance(result,dict):raise ValueError()
            version=result.get('version','')
            version=version if isinstance(version,str) and re.fullmatch(r'[0-9.]{1,40}',version) else 'unknown version'
            installed=result.get('installed') is True
            state['checks']['asideCli']=check('ready' if installed else 'missing','Aside CLI '+version if installed else 'Install or repair the CLI in Aside Developer settings')
            auth=result.get('authentication')
            state['checks']['asideAuth']=check(auth if auth in ('signed-in','signed-out') else 'unknown',{'signed-in':'CLI account signed in; model access not exercised','signed-out':'Sign in through Aside Developer settings'}.get(auth,'Account status could not be determined'))
        except (ValueError,OSError,OperationError):
            state['checks']['asideCli']=check('error','CLI check failed; repair or update Aside CLI')
            state['checks']['asideAuth']=check('unknown','Authentication not checked')
    elif not config['asideEnabled']:
        for key in ('asideCli','asideAuth'):
            state['checks'][key]=check('disabled','Aside integration is disabled')
    elif not deep and previous and state.get('error'):
        for key in ('asideCli','asideAuth'):
            state['checks'][key]=check('error','Connection checks failed; refresh to retry')
    elif not deep and previous and state['checks'].get('ssh',{}).get('status')=='ready':
        for key in ('asideCli','asideAuth'):
            if key in previous.get('checks',{}):state['checks'][key]=previous['checks'][key]
    else:
        for key in ('asideCli','asideAuth'):
            state['checks'][key]=check('unknown','Windows command access is needed to check Aside CLI')
    return state
