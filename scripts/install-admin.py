#!/usr/bin/env python3
"""Explicit interactive installation; no sudoers or polkit policy changes."""
import hashlib
import os
from pathlib import Path
import stat
import tempfile

if os.geteuid()!=0 or int(os.environ.get('SUDO_UID','0'))<1000:
    raise SystemExit('Use sudo /usr/bin/python3 -I scripts/install-admin.py in your terminal')
source=Path(__file__).resolve().parents[1]/'backend/admin.py'
data=source.read_bytes()
compile(data,str(source),'exec')
folder=Path('/usr/local/lib/omachines')
for parent in [Path('/usr/local'),Path('/usr/local/lib'),folder]:
    parent.mkdir(exist_ok=True,mode=0o755)
    s=parent.lstat()
    if stat.S_ISLNK(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022:
        raise SystemExit('Unsafe administrator installation directory')
target=folder/'admin.py'
if target.is_symlink():raise SystemExit('Refusing a symlinked administrator helper')
if target.exists():
    old=target.read_bytes();backup=folder/('admin.previous-'+hashlib.sha256(old).hexdigest()[:12]+'.py')
    if not backup.exists():
        with backup.open('xb') as f:f.write(old)
        backup.chmod(0o600)
fd,name=tempfile.mkstemp(dir=folder,prefix='.install-')
try:
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    os.chmod(name,0o755);os.chown(name,0,0);os.replace(name,target)
finally:Path(name).unlink(missing_ok=True)
print('Omachines administrator helper installed. SHA-256:',hashlib.sha256(data).hexdigest())
print('No automatic or passwordless administrator access was granted.')
