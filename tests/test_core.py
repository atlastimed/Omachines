import importlib.machinery
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import core
import operations


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'XDG_STATE_HOME':str(self.path)})
        self.env.start()
        self.only_windows=patch('machines.available',lambda m:m['provider']=='omarchy-windows')
        self.only_windows.start()

    def tearDown(self):
        self.only_windows.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_private_atomic_json(self):
        path=core.state_dir()/'test.json'
        core.write_json(path,{'value':1})
        core.write_json(path,{'value':2})
        self.assertEqual(core.read_json(path),{'value':2})
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777,0o700)

    def test_symlink_state_rejected(self):
        target=self.path/'target'
        target.mkdir()
        (self.path/'omachines').symlink_to(target,target_is_directory=True)
        with self.assertRaises(core.OperationError):core.state_dir()

    def test_settings_bounded_and_typed(self):
        with patch('core.read_json',return_value={}):
            config=core.settings({'pollSeconds':1,'notify':'false','asideEnabled':'yes'})
        self.assertEqual(config['pollSeconds'],30)
        self.assertFalse(config['notify'])
        self.assertTrue(config['asideEnabled'])

    def test_guest_allowlist(self):
        data=core.parse_guest({'desktop':'invented','cpu':True,'password':'NEVER','asidePath':'C:\\evil.exe'})
        self.assertEqual(data['desktop'],'unknown')
        self.assertIsNone(data['cpu'])
        self.assertNotIn('password',data)
        self.assertEqual(data['asidePath'],'')

    def test_aside_path_only_expected_install(self):
        good=r'C:\Users\Example\AppData\Local\Aside\Application\aside.exe'
        self.assertEqual(core.parse_guest({'asidePath':good})['asidePath'],good)
        self.assertEqual(core.parse_guest({'asidePath':good+' --bad'})['asidePath'],'')

    def test_unknown_is_not_stopped(self):
        with patch('core.tcp',return_value=False),patch('core.viewer_windows',return_value=[]),patch('core.backups',return_value=[]):
            state=core.collect(core.settings())
        self.assertEqual(state['state'],'unreachable')
        self.assertEqual(state['checks']['ssh']['status'],'offline')

    def test_successful_stop_records_stopped(self):
        operations.record('stop','complete','Done')
        with patch('core.tcp',return_value=False),patch('core.viewer_windows',return_value=[]),patch('core.backups',return_value=[]):
            state=core.collect(core.settings())
        self.assertEqual(state['state'],'stopped')

    def test_interrupted_operation_not_running(self):
        operations.record('backup','running','Copying',pid=999999999)
        self.assertEqual(core.operation()['status'],'interrupted')

    def test_pending_start_expires(self):
        operations.record('backup','starting','Waiting',startedEpoch=0)
        self.assertEqual(core.operation()['status'],'interrupted')

    def test_stale_result_preserves_timestamp(self):
        previous={'state':'running','checks':{'ssh':{'status':'ready','checkedAt':'old'}},'resources':{'cpu':4}}
        with patch('core.tcp',return_value=True),patch('core.viewer_windows',return_value=[]),patch('core.backups',return_value=[]):
            state=core.collect(core.settings(),previous=previous)
        self.assertEqual(state['checks']['ssh']['checkedAt'],'old')

    def test_stale_result_never_claims_ready(self):
        previous={'state':'running','summary':'Windows is ready','checks':{'ssh':{'status':'ready','checkedAt':'2000-01-01T00:00:00+00:00'}}}
        with patch('core.tcp',return_value=True),patch('core.viewer_windows',return_value=[]),patch('core.backups',return_value=[]):
            state=core.collect(core.settings(),previous=previous)
        self.assertIn('checks are due',state['summary'])

    def test_backup_offline_is_expected(self):
        operations.record('backup','running','Copying')
        with patch('core.operation_locked',return_value=True),patch('core.tcp',return_value=False),patch('core.viewer_windows',return_value=[]),patch('core.backups',return_value=[]):
            state=core.collect(core.settings())
        self.assertEqual(state['summary'],'Backup in progress')
        self.assertEqual(state['checks']['desktop']['message'],'Unavailable during backup')

    def test_live_worker_lock_blocks_requests(self):
        import fcntl
        with open(core.state_dir()/'operation.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(core.OperationError):operations.request_action('open',{'demo':False})

    def test_release_staging_uses_real_content_and_excludes_secrets(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        import install
        dest=self.path/'release';dest.mkdir();install.stage_release(dest)
        manifest=json.loads((dest/'manifest.json').read_text())
        runtime=(dest/manifest['entryPoints']['service']).parent
        self.assertEqual((runtime/'Service.qml').read_bytes(),(install.ROOT/'Service.qml').read_bytes())
        self.assertEqual((runtime/'backend/operations.py').read_bytes(),(install.ROOT/'backend/operations.py').read_bytes())
        # Every worker provider runs under the supervisor.
        self.assertTrue((runtime/'backend/supervise.py').is_file())
        self.assertFalse(list(dest.rglob('__pycache__')))
        self.assertFalse(list(dest.rglob('.env')))
        second=self.path/'second';second.mkdir();install.stage_release(second)
        self.assertEqual(manifest['entryPoints'],json.loads((second/'manifest.json').read_text())['entryPoints'])

    def test_failed_ssh_invalidates_guest_data(self):
        with patch('core.tcp',return_value=True),patch('core.viewer_windows',return_value=[]),patch('core.backups',return_value=[]),patch('core.capabilities',return_value={'ssh':True}),patch('core.guest_status',side_effect=core.OperationError('private output')):
            state=core.collect(core.settings(),deep=True)
        self.assertEqual(state['resources'],{})
        self.assertNotIn('private output',json.dumps(state))

    def test_no_verified_backup_from_directory_alone(self):
        work=self.path/'setup';(work/'research').mkdir(parents=True)
        backup=self.path/'backup';backup.mkdir()
        (work/'research/baseline.path').write_text(str(backup))
        (work/'research/baseline.status').write_text('complete')
        records=core.backups({'setupWorkspace':str(work)})
        self.assertFalse(records[0]['verified'])

    def test_demo_never_invokes_vm(self):
        with patch('core.tcp',side_effect=AssertionError('network')):
            self.assertTrue(core.collect({'demo':True})['demo'])
        with self.assertRaises(core.OperationError):operations.request_action('open',{'demo':True})

    def test_action_allowlist_and_confirmation(self):
        with self.assertRaises(core.OperationError):operations.request_action('rm -rf',{'demo':False})
        for action in operations.DISRUPTIVE:
            with self.assertRaises(core.OperationError):operations.request_action(action,{'demo':False})

    def test_repeated_requests_are_rejected(self):
        with patch('operations.subprocess.Popen') as launch:
            operations.request_action('open',{'demo':False})
            with self.assertRaises(core.OperationError):operations.request_action('start',{'demo':False})
            launch.assert_called_once()

    def test_focus_existing_viewer_no_duplicate(self):
        with patch('operations.viewer_windows',return_value=['0x123']),patch('operations.run') as run,patch('operations.subprocess.Popen') as launch:
            operations.perform('open',{})
        run.assert_called_once_with(['hyprctl','dispatch','hl.dsp.focus({ window = "address:0x123" })'],3)
        launch.assert_not_called()

    def test_diagnostic_excludes_private_fields(self):
        state=core.sample_state();state['guest']={'password':'SECRET','asidePath':'PRIVATE'}
        state['backups']=[{'path':'PRIVATE'}]
        with patch('operations.collect',return_value=state):path=operations.write_diagnostics({})
        text=path.read_text()
        self.assertNotIn('PRIVATE',text);self.assertNotIn('SECRET',text)

    def test_old_backup_provider_refused(self):
        work=self.path/'setup';work.mkdir();(work/'create-development-backup.sh').write_text('exit 0')
        with self.assertRaises(core.OperationError):operations.backup({'setupWorkspace':str(work)})


if __name__=='__main__':unittest.main()
