#!/usr/bin/env python3
"""Validate locally without reading Windows, changing settings or stopping VMs."""
import os
import json
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SHELL=Path('/usr/share/omarchy/shell')


def run(*args,**kwargs):
    print('+ '+' '.join(map(str,args)),flush=True)
    subprocess.run(list(map(str,args)),cwd=ROOT,check=True,**kwargs)


env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
# The tests start processes and send signals. On 8 November 2026 a mocked pid
# reached kill(-1) and killed the desktop session running them. In its own PID
# namespace a test can signal only the suite, and without the session's variables
# or the real home it cannot reach Hyprland, D-Bus or the VM wrappers.
SEAL=['unshare','--user','--map-current-user','--pid','--fork','--kill-child','--mount-proc']
if subprocess.run([*SEAL,'true'],capture_output=True).returncode:
    raise SystemExit('Refusing to run the tests: this system does not allow a private PID namespace (unshare).')
with tempfile.TemporaryDirectory(prefix='omachines-test-home-') as home:
    sealed={k:v for k,v in env.items() if k not in ('HYPRLAND_INSTANCE_SIGNATURE','WAYLAND_DISPLAY','DISPLAY','DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR','XDG_STATE_HOME','XDG_CONFIG_HOME')}
    run(*SEAL,sys.executable,'-m','unittest','discover','-s','tests','-v',env={**sealed,'HOME':home})
run('omarchy','plugin','validate',ROOT)
with tempfile.TemporaryDirectory(prefix='omachines-qt-') as tmp:
    imports=Path(tmp)
    (imports/'qs').symlink_to(SHELL,target_is_directory=True)
    lint=subprocess.run(['/usr/lib/qt6/bin/qmllint','--json','-','--unqualified','info','--max-warnings','0','-I',str(imports),*map(str,sorted(ROOT.glob('*.qml')))],capture_output=True,text=True)
    # Some Qt releases omit warnings from human-readable output while still
    # failing --max-warnings. JSON is the stable diagnostic source.
    diagnostics=json.loads(lint.stdout)
    for file in diagnostics['files']:
        for warning in file.get('warnings',[]):
            print(f"{file['filename']}:{warning.get('line',0)}: {warning['type']}: {warning['message']}")
    if lint.returncode:raise SystemExit('QML lint failed')
    qtenv={**env,'QML_IMPORT_PATH':str(imports),'QT_QPA_PLATFORM':'offscreen','QT_QPA_PLATFORMTHEME':'','QT_QUICK_BACKEND':'software','QT_QUICK_CONTROLS_STYLE':'Basic'}
    # Quickshell registers its native types in its executable, not loadable Qt
    # plugins. Run QtTest inside qs and explicitly start its test case schedule.
    qtenv.pop('WAYLAND_DISPLAY',None)
    # qs maps the special qs.* namespace to its config root. Stage the real
    # Omarchy components and plugin view under one disposable runtime root.
    for item in SHELL.iterdir():
        if item.is_dir(): (imports/item.name).symlink_to(item,target_is_directory=True)
    for item in [*ROOT.glob('*.qml'),*ROOT.glob('*.js')]: shutil.copy2(item,imports/item.name)
    (imports/'PanelTests.qml').write_text((ROOT/'tests/tst_panel.qml').read_text().replace('import ".." as App','import "." as App').replace('import "../Model.js"','import "Model.js"'))
    result=subprocess.run(['qs','--no-color','-p',str(imports/'PanelTests.qml')],cwd=ROOT,env=qtenv,capture_output=True,text=True,timeout=40)
    output=result.stdout+result.stderr
    print(output)
    matched=re.search(r'OMACHINES_TEST_RESULT (\d+) (\d+)',output)
    expected=len(re.findall(r'function test_', (ROOT/'tests/tst_panel.qml').read_text()))
    if result.returncode or not matched or int(matched[1]) or int(matched[2])<expected:
        raise SystemExit('Native QML interaction tests failed or did not run')
print('Checks passed')
