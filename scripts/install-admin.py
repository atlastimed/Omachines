#!/usr/bin/env python3
"""Explicit interactive installation of Omachines' root-owned pieces.

Always: the administrator helper, and a docker.service drop-in so the PC waits
for Windows to shut down. Only with --allow-windows-start: a polkit rule that
lets this user start and stop Windows without a password. Nothing else is
authorized, and backup, restore and allocation still ask every time.
"""
import argparse
import hashlib
import os
from pathlib import Path
import pwd
import stat
import subprocess
import tempfile

FOLDER=Path('/usr/local/lib/omachines')
DROPIN=Path('/etc/systemd/system/docker.service.d/omachines-windows-shutdown.conf')
RULE=Path('/etc/polkit-1/rules.d/49-omachines-windows.rules')
MARKER=FOLDER/'windows-start-allowed'
COMMAND='/usr/bin/omarchy-windows-vm'
ALLOWED=('up','up_wait','down')


def write(path,text,mode=0o644):
    # Refuses a symlink in the final component; parents here are root-owned.
    if path.is_symlink():raise SystemExit(f'Refusing a symlink at {path}')
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.omachines-')
    try:
        with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
        os.chmod(name,mode);os.chown(name,0,0);os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)


def shutdown_dropin():
    # Omarchy's DefaultTimeoutStopSec=5s also applies to Docker, which then gets
    # killed while it waits for Windows (stop_grace_period 2m). A stopped Docker
    # or an empty one exits at once, so this delays only a PC shutdown that has a
    # VM to wait for. Ordering after local-fs.target keeps the VM drive mounted
    # until Docker is done: Omarchy's DefaultDependencies=no drop-in removed that.
    return ('# Written by Omachines scripts/install-admin.py.\n'
            '# Let Docker finish stopping the Windows VM when this PC shuts down.\n'
            '[Unit]\nAfter=local-fs.target\n\n[Service]\nTimeoutStopSec=135\n')


def rule_text(user):
    commands=', '.join(f'"{COMMAND} __priv {action}"' for action in ALLOWED)
    return ('// Written by Omachines scripts/install-admin.py --allow-windows-start.\n'
            '// Starts and stops the existing Windows VM without a password for this\n'
            '// user\'s active session. These exact commands take no input and act only\n'
            '// on the root-owned compose file. Remove with --revoke-windows-start.\n'
            'polkit.addRule(function(action, subject) {\n'
            '  if (action.id == "org.freedesktop.policykit.exec" &&\n'
            f'      action.lookup("program") == "{COMMAND}" &&\n'
            f'      subject.user == "{user}" && subject.active &&\n'
            f'      [{commands}].indexOf(action.lookup("command_line")) >= 0) {{\n'
            '    return polkit.Result.YES;\n'
            '  }\n'
            '});\n')


parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
group=parser.add_mutually_exclusive_group()
group.add_argument('--allow-windows-start',action='store_true',help='let this user start and stop Windows without a password')
group.add_argument('--revoke-windows-start',action='store_true',help='remove that permission again')
args=parser.parse_args()
if os.geteuid()!=0 or int(os.environ.get('SUDO_UID','0'))<1000:
    raise SystemExit('Use sudo /usr/bin/python3 -I scripts/install-admin.py in your terminal')
source=Path(__file__).resolve().parents[1]/'backend/admin.py'
data=source.read_bytes()
compile(data,str(source),'exec')
for parent in [Path('/usr/local'),Path('/usr/local/lib'),FOLDER]:
    parent.mkdir(exist_ok=True,mode=0o755)
    s=parent.lstat()
    if stat.S_ISLNK(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022:
        raise SystemExit('Unsafe administrator installation directory')
target=FOLDER/'admin.py'
if target.is_symlink():raise SystemExit('Refusing a symlinked administrator helper')
if target.exists():
    old=target.read_bytes();backup=FOLDER/('admin.previous-'+hashlib.sha256(old).hexdigest()[:12]+'.py')
    if not backup.exists():
        with backup.open('xb') as f:f.write(old)
        backup.chmod(0o600)
fd,name=tempfile.mkstemp(dir=FOLDER,prefix='.install-')
try:
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    os.chmod(name,0o755);os.chown(name,0,0);os.replace(name,target)
finally:Path(name).unlink(missing_ok=True)
print('Omachines administrator helper installed. SHA-256:',hashlib.sha256(data).hexdigest())

DROPIN.parent.mkdir(exist_ok=True,mode=0o755)
write(DROPIN,shutdown_dropin())
subprocess.run(['/usr/bin/systemctl','daemon-reload'],check=True)
print('This PC now waits up to 135 seconds for Docker to stop the Windows VM when it shuts down.')

if args.allow_windows_start:
    user=pwd.getpwuid(int(os.environ['SUDO_UID'])).pw_name
    write(RULE,rule_text(user))
    write(MARKER,'Starting and stopping Windows is allowed without a password; see '+str(RULE)+'\n')
    print(f'{user} can now start and stop Windows without a password. Backup, restore and allocation still ask.')
elif args.revoke_windows_start:
    RULE.unlink(missing_ok=True);MARKER.unlink(missing_ok=True)
    print('Starting and stopping Windows asks for a password again.')
elif MARKER.exists():
    print('Starting and stopping Windows stays allowed without a password (--revoke-windows-start removes this).')
else:
    print('No passwordless administrator access was granted.')
