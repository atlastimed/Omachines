#!/usr/bin/env python3
"""Interactive administrator operations for the existing Omarchy Windows VM.

Install root-owned; invoke with sudo python3 -I. No imported project code, shell
evaluation, credential output, passwordless policy, or arbitrary path arguments.
"""
import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import time

VERSION="0.3.1"
IMAGE_LIMIT=64*1024*1024
COMPOSE=Path("/var/lib/omarchy/windows/docker-compose.yml")
HELPER=Path("/usr/local/lib/omachines/admin.py")
LOCK=Path("/run/lock/omarchy-windows-vm")
GIB=1024**3
BACKUP_NAME=re.compile(r"(?:baseline|development-ready|omachines)-\d{8}T\d{6}Z(?:-[0-9a-f]{6})?")


class Refused(Exception): pass


def stamp(): return dt.datetime.now(dt.timezone.utc).isoformat()
def token(): return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+os.urandom(3).hex()


def command(argv, timeout=60, pass_fds=()):
    try:
        p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout,pass_fds=pass_fds,env={"PATH":"/usr/bin:/usr/sbin:/bin:/sbin","LC_ALL":"C.UTF-8"})
    except (OSError,subprocess.TimeoutExpired):
        raise Refused("A system command failed or timed out; inspect the operation before retrying") from None
    if p.returncode:
        raise Refused("A system command failed; original data and incomplete work were retained")
    return p.stdout


def typed():
    try:return input().strip()
    except EOFError:return ''


def mounted(path):
    # os.path.ismount compares devices, so it misses a bind mount within one
    # filesystem. The kernel's mount table lists every mount point (field 5,
    # with octal escapes for spaces).
    try:
        with open("/proc/self/mountinfo") as f:
            points={re.sub(r"\\([0-7]{3})",lambda m:chr(int(m.group(1),8)),fields[4]) for fields in (line.split() for line in f) if len(fields)>4}
    except OSError:
        raise Refused("The mount table could not be read; nothing was switched") from None
    return str(path) in points


def trusted(path):
    path=Path(path)
    for p in (path,*path.parents):
        s=p.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022:
            raise Refused("Administrator paths must be root-owned and not writable by other users")
    return path


def atomic(path,data,gid=0):
    fd,name=tempfile.mkstemp(prefix=".omachines-",dir=path.parent)
    try:
        with os.fdopen(fd,"w") as f:
            json.dump(data,f);f.write("\n");f.flush();os.fsync(f.fileno())
        os.chown(name,0,gid);os.chmod(name,0o640)
        os.replace(name,path)
        d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(d)
        finally: os.close(d)
    finally:
        Path(name).unlink(missing_ok=True)


def copy_image(source,target,gid):
    # The rehearsal container wrote source: never follow a link it left there,
    # and copy only a plain file of screenshot size.
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as f:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>IMAGE_LIMIT:raise Refused('The rehearsal screen capture is not a plain image file')
        data=f.read(IMAGE_LIMIT+1)
    if len(data)>IMAGE_LIMIT:raise Refused('The rehearsal screen capture is not a plain image file')
    out,name=tempfile.mkstemp(prefix='.omachines-',dir=target.parent)
    try:
        with os.fdopen(out,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.chown(name,0,gid);os.chmod(name,0o640);os.replace(name,target)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def lifecycle_lock():
    trusted(LOCK)
    fd=os.open(LOCK,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise Refused("Another Windows lifecycle operation is running") from None
        yield
    finally: os.close(fd)


def safe_member(member):
    p=PurePosixPath(member.name)
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0]!="storage" or "\x00" in member.name:
        raise Refused("Archive contains a path outside its storage directory")
    if not (member.isfile() or member.isdir()) or member.size<0:
        raise Refused("Archive contains links or unsupported entries")
    if member.mode&0o7000:
        raise Refused("Archive contains unsafe permission bits")


def archive_info(path):
    required={"storage/data.img"}; names=set(); files=set(); logical=0; required_bytes=0
    with tarfile.open(path,"r:") as tar:
        for member in tar:
            safe_member(member)
            name=str(PurePosixPath(member.name))
            if name in names: raise Refused("Archive contains duplicate entries")
            names.add(name);logical+=member.size
            if member.isfile() and member.size>0: files.add(name)
            required_bytes+=sum(length for _,length in member.sparse) if member.sparse is not None else member.size
            if len(names)>100000 or logical>4*1024*GIB: raise Refused("Archive exceeds supported limits")
    if not required.issubset(files): raise Refused("Archive has no Windows disk")
    if not ({"storage/windows_secure.vars","storage/windows_secure.rom"}<=files or {"storage/windows.vars","storage/windows.rom"}<=files):
        raise Refused("Archive has no complete firmware pair")
    if "storage/windows_secure.vars" in files and "storage/windows_secure.tpm" not in files and not any(n.startswith("storage/windows_secure.tpm/") for n in files):
        raise Refused("Secure Windows archive has no TPM state")
    return {"logicalBytes":logical,"requiredBytes":required_bytes,"entries":len(names)}


def checksum(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()


def checked_backup(base,name):
    if not isinstance(name,str) or not BACKUP_NAME.fullmatch(name): raise Refused("Select a known backup")
    path=base/name
    if path.is_symlink() or path.resolve()!=base.resolve()/name or not path.is_dir(): raise Refused("Backup path is not a regular directory")
    for entry in (path,path/"storage.tar",path/"SHA256SUMS"):
        s=entry.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022: raise Refused("Backup contents must be root-owned and protected from writes")
    if not (path/"storage.tar").is_file() or not (path/"SHA256SUMS").is_file(): raise Refused("Backup files missing")
    match=re.fullmatch(r"([0-9a-f]{64}) [ *]storage\.tar\s*",(path/"SHA256SUMS").read_text())
    if not match: raise Refused("Backup checksum record is invalid")
    return path,match[1]


def memory_info():
    return {l.split(':')[0]:int(l.split()[1])*1024 for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith(('MemTotal:','MemAvailable:'))}


def validate_allocation(cpu,ram,total_cpu,total_memory):
    if type(cpu)!=int or type(ram)!=int or not 1<=cpu<=total_cpu or not 4<=ram<=256:
        raise Refused("Choose at least 4 GiB RAM and a CPU count within the host capacity")
    if ram*GIB>total_memory-max(4*GIB,total_memory//4):
        raise Refused("Leave at least 4 GiB and one quarter of host RAM for Omarchy")


def replace_allocation(text,cpu,ram):
    for key,value in (("CPU_CORES",str(cpu)),("RAM_SIZE",str(ram)+"G")):
        pattern=r'(?m)^(\s{6}'+key+r':)[^\r\n]*$'
        if len(re.findall(pattern,text))!=1: raise Refused("Unrecognised compose layout; no changes made")
        text=re.sub(pattern,lambda m:m[1]+' "'+value+'"',text)
    return text


class Provider:
    def __init__(self,uid,recovery=False):
        self.opened=[]
        self.uid=uid;account=pwd.getpwuid(uid);self.gid=account.pw_gid;self.home=Path(account.pw_dir)
        trusted(self.home.parent);trusted(COMPOSE)
        for parent in (Path('/var/lib/omachines'),Path('/var/lib/omachines/users')):
            parent.mkdir(exist_ok=True,mode=0o755);trusted(parent)
            # The process umask is deliberately private. These two directories
            # contain only root-owned per-user directories and need traversal.
            parent.chmod(0o755)
        self.root=Path("/var/lib/omachines/users")/str(uid)
        self.root.mkdir(parents=True,exist_ok=True,mode=0o750)
        trusted(self.root);os.chown(self.root,0,self.gid);os.chmod(self.root,0o750)
        self.state_path=self.root/"state.json"
        self.state=json.loads(self.state_path.read_text()) if self.state_path.exists() else {"schemaVersion":1,"backups":[]}
        self.display_base=self.home/"Windows-VM-Backups"
        self.display_base.mkdir(exist_ok=True,mode=0o700)
        if self.display_base.is_symlink() or self.display_base.resolve()!=self.display_base: raise Refused("Backup root must not be a symlink")
        basefd=os.open(self.display_base,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        self.opened.append(basefd)
        # The home is user-writable. Pin the directory so renames/symlink swaps
        # cannot redirect privileged writes into an arbitrary host directory.
        self.base=Path('/proc/self/fd')/str(basefd)
        self.service=json.loads(command(["/usr/bin/docker","compose","-f",str(COMPOSE),"config","--format","json"]))["services"]["windows"]
        self.anchor=Path("/var/lib/omarchy/windows/mounts/users")/str(uid)/"storage"
        matches=[v for v in self.service.get("volumes",[]) if v.get("target")=="/storage"]
        if len(matches)!=1 or matches[0].get("source")!=str(self.anchor): raise Refused("Update Omarchy's protected Windows storage mounts before using this provider")
        # The mount leaf deliberately has the user's ownership. Its ancestry is
        # root-protected, and it must pin the same inode as the user's VM source.
        trusted(self.anchor.parent)
        self.storage=(self.home/".windows").resolve(strict=not recovery)
        if recovery:
            original=self.state.get('recovery',{}).get('original','')
            if not original or self.storage!=Path(original):raise Refused('Recovery target does not match the recorded storage path')
            trusted(self.storage.parent)
        elif not self.storage.is_dir() or not os.path.samefile(self.storage,self.anchor): raise Refused("Windows storage mount identity changed")
        if self.service.get("container_name")!="omarchy-windows": raise Refused("Unsupported Windows container identity")

    def save(self):
        self.state["updatedAt"]=stamp();atomic(self.state_path,self.state,self.gid)

    def stage(self,name): print("OMACHINES_STAGE:"+name,flush=True)

    def running(self):
        return bool(command(["/usr/bin/docker","ps","-q","--filter","name=^/omarchy-windows$"]).strip())

    def down(self):
        self.stage("stopping")
        command(["/usr/bin/docker","compose","-f",str(COMPOSE),"down","--timeout","120"],160)
        if self.running(): raise Refused("Windows did not stop")

    def up(self):
        self.stage("starting")
        command(["/usr/bin/env",f"PKEXEC_UID={self.uid}","/usr/bin/omarchy-windows-vm","__priv","up"],180)

    def record_backup(self,path,**values):
        rows=self.state.setdefault("backups",[])
        row=next((r for r in rows if r["id"]==path.name),None)
        if row is None:
            row={"id":path.name,"path":str(self.display_base/path.name),"createdAt":dt.datetime.fromtimestamp(path.stat().st_mtime,dt.timezone.utc).isoformat(),"restoreTested":False}
            rows.append(row)
        row.update(values);self.save();return row

    def inspect(self):
        env=self.service.get("environment",{})
        self.state["allocation"]={"cpu":int(env.get("CPU_CORES",0)),"ram":env.get("RAM_SIZE","unknown")}
        self.state["storage"]={"path":str(self.storage),"availableBytes":shutil.disk_usage(self.storage).free}
        self.state["backupSpaceBytes"]=shutil.disk_usage(self.base).free
        self.state["providerVersion"]=VERSION
        for path in self.base.iterdir():
            if BACKUP_NAME.fullmatch(path.name):
                try:
                    path,_=checked_backup(self.base,path.name)
                    self.record_backup(path,bytes=(path/"storage.tar").stat().st_size)
                except (OSError,ValueError,Refused): pass
        self.save();return "Windows configuration and backup catalogue refreshed"

    def verify(self,name):
        self.stage("verifying");path,expected=checked_backup(self.base,name)
        directory=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            fd=os.open('storage.tar',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
            info_stat=os.fstat(fd)
            if not stat.S_ISREG(info_stat.st_mode) or info_stat.st_uid!=0 or info_stat.st_mode&0o022:
                os.close(fd);raise Refused('Backup archive identity changed')
        finally:os.close(directory)
        self.opened.append(fd)
        pinned=Path('/proc/self/fd')/str(fd)
        # Invalidate old evidence before doing work, including when interrupted.
        self.record_backup(path,verified=False,verificationSource="Integrity check in progress")
        if checksum(pinned)!=expected:
            self.record_backup(path,verified=False,verificationSource="Checksum mismatch")
            raise Refused("Backup checksum mismatch; restore refused")
        info=archive_info(pinned)
        old=next((r for r in self.state['backups'] if r['id']==name),{})
        self.record_backup(path,verified=True,verifiedAt=stamp(),sha256=expected,restoreTested=old.get('restoreTested',False) and old.get('sha256')==expected,verificationSource="SHA-256 and archive structure checked",bytes=info_stat.st_size,**info)
        return path,info,fd

    def backup(self):
        if self.storage.name!='storage':raise Refused('Unsupported storage directory name; nothing was stopped')
        name="omachines-"+token();partial=self.base/(name+".incomplete");dest=self.base/name
        needed=sum(p.stat().st_blocks*512 for p in self.storage.rglob('*') if p.is_file())
        if shutil.disk_usage(self.base).free<needed+10*GIB: raise Refused("Not enough backup space; nothing was stopped")
        was_running=False
        try:
            with lifecycle_lock():
                # Staged only once both locks are held: a refused backup leaves nothing behind.
                partial.mkdir(mode=0o700)
                partial_fd=os.open(partial,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                partial_stat=os.fstat(partial_fd)
                if partial_stat.st_uid!=0 or partial_stat.st_mode&0o077:
                    os.close(partial_fd);raise Refused('Backup staging identity changed')
                self.opened.append(partial_fd)
                pinned_partial=Path('/proc/self/fd')/str(partial_fd)
                was_running=self.running();self.down()
                self.stage("copying")
                command(["/usr/bin/tar","--sparse","--hole-detection=seek","-cpf",str(pinned_partial/"storage.tar"),"-C",str(self.storage.parent),self.storage.name],3600,pass_fds=tuple(self.opened))
                # Standard archive root is storage. Refuse another source name
                # rather than quietly producing an incompatible recovery file.
                info=archive_info(pinned_partial/"storage.tar")
                (pinned_partial/"config").mkdir(mode=0o700);shutil.copy2(COMPOSE,pinned_partial/"config/docker-compose.yml")
                self.stage("verifying");digest=checksum(pinned_partial/"storage.tar")
                (pinned_partial/"SHA256SUMS").write_text(digest+"  storage.tar\n")
                command(["/usr/bin/sync","-f",str(pinned_partial/"storage.tar")],pass_fds=tuple(self.opened))
                if not os.path.samestat(partial.lstat(),partial_stat):raise Refused('Backup directory moved during copying; inspect retained data')
                partial.rename(dest)
                if not os.path.samestat(dest.lstat(),partial_stat):raise Refused('Backup directory moved during completion; inspect retained data')
                self.record_backup(dest,verified=True,verifiedAt=stamp(),sha256=digest,bytes=(pinned_partial/"storage.tar").stat().st_size,verificationSource="SHA-256 and archive structure checked",**info)
        finally:
            if was_running:self.up()
        return "Complete backup saved and verified"

    def allocate(self,cpu,ram):
        memory=memory_info()
        validate_allocation(cpu,ram,os.cpu_count() or 1,memory['MemTotal'])
        previous_ram=str(self.service.get('environment',{}).get('RAM_SIZE',''))
        if not re.fullmatch(r'\d+G',previous_ram):raise Refused('Unsupported existing RAM allocation')
        was_running=self.running()
        extra=max(0,ram-int(previous_ram[:-1])) if was_running else ram
        if extra*GIB>memory['MemAvailable']-2*GIB:raise Refused('Not enough currently available RAM; nothing was stopped')
        text=COMPOSE.read_text();new=replace_allocation(text,cpu,ram)
        if new==text:return "Allocation is already configured; Windows was not restarted"
        fd,temp=tempfile.mkstemp(dir=COMPOSE.parent,prefix='.omachines-compose-')
        try:
            with os.fdopen(fd,'w') as f:f.write(new);f.flush();os.fsync(f.fileno())
            os.chmod(temp,COMPOSE.stat().st_mode&0o777);os.chown(temp,0,COMPOSE.stat().st_gid)
            # Validate before closing any user applications.
            command(["/usr/bin/docker","compose","-f",temp,"config","--quiet"])
            with lifecycle_lock():
                if COMPOSE.read_text()!=text:raise Refused('Windows configuration changed; request allocation again')
                previous=self.root/("compose-before-"+token()+".yml");previous.write_text(text);previous.chmod(0o600)
                was_running=self.running()
                self.down()
                try:os.replace(temp,COMPOSE)
                except BaseException:
                    self.state['allocationRecovery']='Previous configuration retained; start Windows again';self.save();raise
                self.state['allocation']={'cpu':cpu,'ram':str(ram)+'G'};self.save()
        finally:Path(temp).unlink(missing_ok=True)
        if was_running:
            try:self.up()
            except Refused:
                with lifecycle_lock():
                    self.down()
                    # Restore only if this is still our change, never overwrite
                    # an independently edited configuration.
                    if COMPOSE.read_text()!=new:raise Refused('Startup failed and configuration changed; inspect before recovery')
                    fd,temp=tempfile.mkstemp(dir=COMPOSE.parent,prefix='.omachines-rollback-')
                    with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
                    os.chmod(temp,COMPOSE.stat().st_mode&0o777);os.chown(temp,0,COMPOSE.stat().st_gid)
                    os.replace(temp,COMPOSE)
                    self.state['allocation']={'cpu':int(self.service['environment']['CPU_CORES']),'ram':previous_ram};self.save()
                self.up()
                raise Refused('New allocation could not start; previous configuration restored and Windows restarted') from None
        return "Allocation updated"+("; Windows started" if was_running else "; applies when Windows starts")

    def room(self,info,parent):
        # Sparse extents determine the required allocation; regular archives
        # require their full logical size.
        if shutil.disk_usage(parent).free<info['requiredBytes']+5*GIB:raise Refused("Not enough space for a safe restore alongside the existing data")

    def extract(self,path,info,parent):
        # path is a pinned, verified archive FD. A failed extraction removes its
        # own staging directory: a fresh copy of a backup that still exists.
        self.room(info,parent)
        stage=Path(tempfile.mkdtemp(prefix='.omachines-restore-',dir=parent));stage.chmod(0o700)
        try:
            self.stage('restoring')
            command(['/usr/bin/tar','--extract','--sparse','--no-same-owner','--no-same-permissions','--file',str(path),'--directory',str(stage)],3600,pass_fds=(int(path.name),))
            command(['/usr/bin/sync','-f',str(stage)])
            clone=stage/'storage'
            if clone.is_symlink() or not clone.is_dir():raise Refused('The extracted backup has no Windows storage; nothing was switched')
        except BaseException:
            shutil.rmtree(stage,ignore_errors=True);raise
        return clone

    def rehearsal(self,name):
        path,info,fd=self.verify(name)
        base=self.home.parent/'.omachines-rehearsals'/str(self.uid)
        base.mkdir(parents=True,exist_ok=True,mode=0o700);trusted(base)
        available=memory_info()['MemAvailable']
        if available<6*GIB:raise Refused('Free at least 6 GiB of host RAM before the rehearsal')
        if self.state.get('rehearsal',{}).get('status') in ('starting','awaiting-review'):raise Refused('Finish the previous rehearsal before starting another')
        clone=self.extract(Path('/proc/self/fd')/str(fd),info,base)
        container='omachines-rehearsal-'+str(self.uid)
        # No host shares, published ports, privileged mode, tun device or network.
        # The disk/TPM/firmware are disposable copies. The original VM is untouched.
        image=self.service['image']
        image_id=command(['/usr/bin/docker','image','inspect',image,'--format','{{.Id}}']).strip()
        argv=['/usr/bin/docker','run','--detach','--pull=never','--name',container,'--label','org.omachines.rehearsal=true','--network=none','--device=/dev/kvm','--mount',f'type=bind,src={clone},dst=/storage']
        env=self.service.get('environment',{})
        for k,v in {'VERSION':env.get('VERSION','11'),'BOOT_MODE':env.get('BOOT_MODE','windows_secure'),'DISK_SIZE':env.get('DISK_SIZE','192G'),'RAM_SIZE':'4G','CPU_CORES':'2','NETWORK':'N','QMP':'7100','DISPLAY':'vnc','ARGUMENTS':'-rtc base=localtime,clock=host,driftfix=slew'}.items():argv+=['--env',k+'='+v]
        self.state['rehearsal']={'backupId':name,'path':str(clone),'container':container,'status':'starting','sha256':next(r['sha256'] for r in self.state['backups'] if r['id']==name),'createdAt':stamp()};self.save()
        command(argv+[image_id]);self.stage('booting')
        print('Offline restored copy started. Waiting for Windows to boot...',flush=True)
        time.sleep(60)
        # QMP speaks only inside the isolated container. Capture never exposes a
        # listener on the host and is saved privately for explicit human review.
        code="import socket,json; s=socket.create_connection(('127.0.0.1',7100),5); f=s.makefile('rwb',buffering=0); f.readline(); f.write(b'{\"execute\":\"qmp_capabilities\"}\\n'); f.readline(); f.write(b'{\"execute\":\"screendump\",\"arguments\":{\"filename\":\"/storage/omachines-rehearsal.ppm\"}}\\n'); print(f.readline().decode())"
        command(['/usr/bin/docker','exec',container,'python3','-c',code],15)
        screenshot=self.root/'rehearsal.ppm';copy_image(clone/'omachines-rehearsal.ppm',screenshot,self.gid)
        self.state['rehearsal'].update(status='awaiting-review',screenshot=str(screenshot));self.save()
        return 'Offline copy is ready for visual review; use Open rehearsal image, then record the result'

    def finish_rehearsal(self,passed):
        r=self.state.get('rehearsal',{})
        if r.get('status') not in ('starting','awaiting-review') or (passed and r.get('status')!='awaiting-review'):raise Refused('No rehearsal is waiting for this review')
        container=r['container']
        if container!='omachines-rehearsal-'+str(self.uid):raise Refused('Unexpected rehearsal container')
        existing=command(['/usr/bin/docker','ps','-aq','--filter','name=^/'+container+'$']).strip()
        if existing:
            labels=json.loads(command(['/usr/bin/docker','inspect',container]))[0]['Config']['Labels']
            if labels.get('org.omachines.rehearsal')!='true':raise Refused('Unexpected rehearsal container')
            command(['/usr/bin/docker','stop','--time','90',container],120)
            command(['/usr/bin/docker','rm',container])
        r.update(status='passed' if passed else 'failed',reviewedAt=stamp());self.save()
        current=next((row for row in self.state.get('backups',[]) if row['id']==r['backupId']),{})
        matches=current.get('sha256')==r.get('sha256')
        self.record_backup(self.base/r['backupId'],restoreTested=passed and matches,restoreTestedAt=stamp(),restoreEvidence='Offline copied disk/TPM/firmware boot visually reviewed' if passed else 'Offline boot review failed')
        # Retain the copy for inspection. No automatic deletion of VM data.
        return 'Rehearsal result saved; disposable VM stopped and its files retained'

    def restore(self,name):
        path,info,fd=self.verify(name)
        trusted(self.storage.parent)
        if self.storage.name!='storage':raise Refused('This recovery provider requires a storage directory below a root-owned parent')
        # Confirm before extracting: a cancelled restore leaves no copy behind.
        self.room(info,self.storage.parent)
        print('Type RESTORE '+name+' to replace the Windows disk, TPM and firmware (old data is retained):',flush=True)
        if typed()!='RESTORE '+name:
            raise Refused('Restore cancelled; original Windows data is unchanged')
        old=self.storage.with_name('storage-before-'+token())
        # Held from before extraction until the switch: a busy lock refuses
        # before anything is copied.
        with lifecycle_lock():
            clone=self.extract(Path('/proc/self/fd')/str(fd),info,self.storage.parent)
            try:self.down()
            except BaseException:
                # Nothing was switched: remove our own copy of a backup that still exists.
                shutil.rmtree(clone.parent,ignore_errors=True);raise
            self.state['recovery']={'status':'prepared','original':str(self.storage),'previous':str(old),'staged':str(clone),'backupId':name};self.save()
            command(['/usr/bin/umount',str(self.anchor)])
            try:
                self.storage.rename(old);clone.rename(self.storage)
                os.chown(self.storage,self.uid,self.gid);self.storage.chmod(0o700)
                command(['/usr/bin/mount','--bind',str(self.storage),str(self.anchor)])
                self.state['recovery']['status']='restored';self.save()
            except BaseException:
                if old.exists() and not self.storage.exists():old.rename(self.storage)
                if self.storage.exists():
                    try:command(['/usr/bin/mount','--bind',str(self.storage),str(self.anchor)])
                    except Refused:pass
                self.state['recovery']['status']='needs-attention';self.save();raise
        self.up()
        return 'Storage restored; previous storage retained. Host connections and current allocation were preserved'

    def rollback(self):
        r=self.state.get('recovery',{});old=Path(r.get('previous',''))
        retry=r.get('status')=='rollback-switching'
        if r.get('status') not in ('prepared','restored','needs-attention','rollback-switching') or old.parent!=self.storage.parent or not re.fullmatch(r'storage-before-\d{8}T\d{6}Z-[0-9a-f]{6}',old.name) or old.is_symlink() or (not old.is_dir() and not (retry and self.storage.is_dir())):
            raise Refused('No retained storage is eligible for rollback')
        print('Type ROLLBACK WINDOWS to stop Windows and return to its retained storage:',flush=True)
        if typed()!='ROLLBACK WINDOWS':raise Refused('Rollback cancelled')
        with lifecycle_lock():
            self.down()
            # Remove every layer; a bind left in place would sit hidden under
            # the new one.
            for _ in range(4):
                if not mounted(self.anchor):break
                command(['/usr/bin/umount',str(self.anchor)])
            if mounted(self.anchor):raise Refused('Windows storage is still mounted; nothing was switched')
            retained=Path(r['restoredCopy']) if retry else self.storage.with_name('storage-after-'+token())
            if retained.parent!=self.storage.parent or not re.fullmatch(r'storage-after-\d{8}T\d{6}Z-[0-9a-f]{6}',retained.name):raise Refused('Invalid rollback journal')
            r.update(status='rollback-switching',restoredCopy=str(retained));self.save()
            if old.exists():
                if self.storage.exists():
                    if retained.exists():raise Refused('Rollback has two retained copies; inspect before retrying')
                    self.storage.rename(retained)
                old.rename(self.storage)
            command(['/usr/bin/mount','--bind',str(self.storage),str(self.anchor)])
            r.update(status='rolled-back',restoredCopy=str(retained));self.save()
        self.up()
        return 'Previous Windows storage restored; replaced storage retained'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['inspect','backup','verify','allocate','rehearse','rehearsal-pass','rehearsal-fail','restore','rollback'])
    parser.add_argument('--backup-id',default='');parser.add_argument('--cpu',type=int);parser.add_argument('--ram',type=int)
    args=parser.parse_args()
    if os.geteuid()!=0:raise Refused('Run this helper through the Omachines operation terminal')
    uid=int(os.environ.get('SUDO_UID','0'))
    if uid<1000:raise Refused('An authenticated desktop user is required')
    trusted(HELPER)
    for sig in (signal.SIGTERM,signal.SIGHUP):
        signal.signal(sig,lambda *_: (_ for _ in ()).throw(Refused("Operation interrupted; inspect retained state before retrying")))
    os.umask(0o077)
    p=Provider(uid,recovery=args.action=='rollback')
    # This root-owned lock also serialises direct helper invocations.
    with (p.root/'admin.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Refused('Another administrative operation is active') from None
        if p.state_path.exists():p.state=json.loads(p.state_path.read_text())
        p.stage('preflight')
        if args.action=='inspect':message=p.inspect()
        elif args.action=='backup':message=p.backup()
        elif args.action=='verify':p.verify(args.backup_id);message='Backup checksum and archive structure verified'
        elif args.action=='allocate':message=p.allocate(args.cpu,args.ram)
        elif args.action=='rehearse':message=p.rehearsal(args.backup_id)
        elif args.action.startswith('rehearsal-'):message=p.finish_rehearsal(args.action=='rehearsal-pass')
        elif args.action=='rollback':message=p.rollback()
        else:message=p.restore(args.backup_id)
        p.stage('complete');print(message,flush=True)


if __name__=='__main__':
    try:main()
    except (Refused,OSError,ValueError,KeyError,tarfile.TarError) as e:
        print(str(e) if isinstance(e,Refused) else 'Operation failed safely; inspect retained data before retrying',flush=True)
        raise SystemExit(1)
