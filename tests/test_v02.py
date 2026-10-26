import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import core,history,machines,operations


class NewStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'XDG_STATE_HOME':self.tmp.name});self.env.start()
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def test_registry_migrates_only_once_and_preserves_name(self):
        first=machines.initialize();self.assertEqual(first['selected'],'windows')
        first['machines'][0]['name']='Testing Windows'
        core.write_json(core.state_dir()/'machines.json',first)
        self.assertEqual(machines.initialize()['machines'][0]['name'],'Testing Windows')
    def test_unknown_provider_fails_closed_without_probes(self):
        data=machines.initialize();data['machines'][0]['provider']='unimplemented'
        core.write_json(core.state_dir()/'machines.json',data)
        with patch('core.tcp',side_effect=AssertionError('probe should not run')):
            result=core.collect(core.settings())
        self.assertEqual(result['state'],'unknown');self.assertEqual(result['capabilities'],{})
    def test_history_bounded_allowlist_and_deduplicated(self):
        for i in range(100):history.append({'requestId':str(i),'action':'verify','status':'complete','message':'Done','private':'exclude'})
        self.assertEqual(len(history.entries()),60)
        self.assertNotIn('private',history.entries()[-1])
        history.append({'requestId':'99','status':'failed'})
        self.assertEqual(len(history.entries()),60);self.assertEqual(history.entries()[-1]['status'],'failed')
    def test_request_captures_parameters_before_ui_changes(self):
        p={'cpu':4,'ram':8}
        with patch('operations.subprocess.Popen'):
            operations.request_action('allocate',{'demo':False},True,p)
        p['ram']=16
        self.assertEqual(core.operation()['parameters'],{'cpu':4,'ram':8})
    def test_bad_parameters_never_spawn(self):
        with patch('operations.subprocess.Popen') as launch:
            for action,p in [('restore',{'backupId':'../../bad'}),('allocate',{'cpu':True,'ram':8}),('label',{'backupId':'baseline-20260101T000000Z','label':'bad\nname'}),('start',{'command':'other'})]:
                with self.assertRaises(core.OperationError):operations.request_action(action,{'demo':False},True,p)
            launch.assert_not_called()
    def test_history_survives_worker_completion(self):
        operations.record('verify','starting','Waiting',requestId='example',parameters={'backupId':'baseline-20260101T000000Z'})
        operations.record('verify','running','Checking');operations.record('verify','complete','Done')
        self.assertEqual(history.entries()[-1]['requestId'],'example')
        self.assertNotIn('parameters',history.entries()[-1])

    def test_invalid_registry_is_not_silently_replaced(self):
        path=core.state_dir()/'machines.json';path.write_text('{bad json')
        with self.assertRaises(ValueError):machines.initialize()
        self.assertEqual(path.read_text(),'{bad json')

    def test_missing_ssh_does_not_spawn_a_cli_probe(self):
        with patch('core.wrapper',return_value=None),patch('core.run') as run:
            with self.assertRaises(core.OperationError):core.aside_cli_status()
            run.assert_not_called()

    def test_disconnected_vm_clears_old_aside_cli_readiness(self):
        previous={'checks':{'asideCli':{'status':'ready'},'asideAuth':{'status':'signed-in'}}}
        offline={'state':'unreachable','checks':{'ssh':{'status':'offline'}},'capabilities':{},'backups':[]}
        with patch('core.collect_windows',return_value=offline),patch('core.aside_cli_status') as probe:
            result=core.collect(core.settings(),previous=previous)
            probe.assert_not_called()
        self.assertEqual(result['checks']['asideCli']['status'],'unknown')
        self.assertEqual(result['checks']['asideAuth']['status'],'unknown')
