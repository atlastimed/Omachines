#!/usr/bin/env python3
"""Validate locally without reading Windows, changing settings or stopping VMs."""
import os
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
run(sys.executable,'-m','unittest','discover','-s','tests','-v',env=env)
run('omarchy','plugin','validate',ROOT)
with tempfile.TemporaryDirectory(prefix='omachines-qt-') as tmp:
    imports=Path(tmp)
    (imports/'qs').symlink_to(SHELL,target_is_directory=True)
    run('/usr/lib/qt6/bin/qmllint','--unqualified','info','--max-warnings','0','-I',imports,*sorted(ROOT.glob('*.qml')))
    qtenv={**env,'QML_IMPORT_PATH':str(imports),'QT_QPA_PLATFORM':'offscreen','QT_QPA_PLATFORMTHEME':'','QT_QUICK_BACKEND':'software','QT_QUICK_CONTROLS_STYLE':'Basic'}
    # Quickshell registers its native types in its executable, not loadable Qt
    # plugins. Run QtTest inside qs and explicitly start its test case schedule.
    qtenv.pop('WAYLAND_DISPLAY',None)
    # qs maps the special qs.* namespace to its config root. Stage the real
    # Omarchy components and plugin view under one disposable runtime root.
    for item in SHELL.iterdir():
        if item.is_dir(): (imports/item.name).symlink_to(item,target_is_directory=True)
    for item in [*ROOT.glob('*.qml'),*ROOT.glob('*.js')]: shutil.copy2(item,imports/item.name)
    (imports/'PanelTests.qml').write_text((ROOT/'tests/tst_panel.qml').read_text().replace('import ".." as App','import "." as App'))
    result=subprocess.run(['qs','--no-color','-p',str(imports/'PanelTests.qml')],cwd=ROOT,env=qtenv,capture_output=True,text=True,timeout=40)
    output=result.stdout+result.stderr
    print(output)
    matched=re.search(r'OMACHINES_TEST_RESULT (\d+) (\d+)',output)
    if result.returncode or not matched or int(matched[1]) or int(matched[2])<7:
        raise SystemExit('Native QML interaction tests failed or did not run')
print('Checks passed')
