import fcntl
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import ANY, Mock, patch

BACKEND = Path(__file__).resolve().parents[1]/"backend"
sys.path.insert(0, str(BACKEND))
import core
import operations


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.patch(patch.dict(os.environ, {"XDG_STATE_HOME": str(self.path)}))
        self.config = {"demo": False, "notify": False}
        # Hermetic: the real Omarchy VM control is never consulted.
        self.patch(patch("machines.available", lambda m: m["provider"] == "omarchy-windows"))
        self.patch(patch("operations.settings", return_value=self.config))
        self.launch = self.patch(patch("operations.subprocess.Popen"))
        self.perform = self.patch(patch("operations.perform", return_value="Done"))

    def patch(self, p):
        value = p.start()
        self.addCleanup(p.stop)
        return value

    def reserve(self, action="stop"):
        return operations.request_action(action, self.config, confirmed=True)

    def test_valid_request_consumed_exactly_once(self):
        token = self.reserve()
        self.assertIn(token, self.launch.call_args.args[0])
        self.assertEqual(operations.worker("stop", token), 0)
        self.assertEqual(operations.worker("stop", token), 1)
        self.perform.assert_called_once_with("stop", self.config, {}, ANY)

    def test_worker_starting_before_launcher_returns_waits_for_admission(self):
        threads, results = [], []
        def launch(argv, **_):
            token = argv[argv.index("--request-id")+1]
            thread = threading.Thread(target=lambda: results.append(operations.worker("stop", token)))
            threads.append(thread); thread.start()
            time.sleep(0.08)  # parent still owns the request gate
        self.launch.side_effect=launch
        try:
            self.reserve()
        finally:
            for thread in threads: thread.join(timeout=3)
        self.assertEqual(results, [0])
        self.perform.assert_called_once()

    def test_expired_request_cannot_run(self):
        token = self.reserve()
        with patch("core.time.time", return_value=time.time()+121):
            self.assertEqual(core.operation()["status"], "interrupted")
            self.assertEqual(operations.worker("stop", token), 1)
        self.perform.assert_not_called()

    def test_delayed_workers_cannot_replace_newer_result(self):
        old = self.reserve()
        with patch("core.time.time", return_value=time.time()+121):
            new = self.reserve("start")
            self.assertEqual(operations.worker("stop", old), 1)
            self.assertEqual(operations.worker("start", new), 0)
        current = core.operation()
        self.assertEqual(operations.worker("stop", old), 1)
        self.assertEqual(core.operation(), current)
        self.perform.assert_called_once_with("start", self.config, {}, ANY)

    def test_wrong_action_or_identity_does_not_consume_request(self):
        token = self.reserve()
        self.assertEqual(operations.worker("start", token), 1)
        self.assertEqual(operations.worker("stop", "wrong"), 1)
        self.assertEqual(core.operation()["status"], "starting")
        self.assertEqual(operations.worker("stop", token), 0)

    def test_demo_enabled_before_worker_starts(self):
        token = self.reserve()
        self.config["demo"] = True
        self.assertEqual(operations.worker("stop", token), 1)
        self.assertEqual(core.operation()["status"], "failed")
        self.perform.assert_not_called()

    def test_pending_or_active_operation_blocks_maintenance(self):
        self.reserve()
        with self.assertRaises(core.OperationError), core.maintenance():
            self.fail("admitted maintenance")
        operations.record("stop", "complete", "Done")
        with open(core.state_dir()/"operation.lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(core.OperationError), core.maintenance():
                self.fail("admitted maintenance")

    def test_maintenance_excludes_requests_workers_and_other_maintenance(self):
        with core.maintenance():
            with self.assertRaises(core.OperationError):
                self.reserve()
            self.assertEqual(operations.worker("stop", "old"), 1)
            with self.assertRaises(core.OperationError), core.maintenance():
                self.fail("admitted second maintenance")
        self.assertTrue(self.reserve())
        self.perform.assert_not_called()

    def test_maintenance_failure_releases_exclusion(self):
        with self.assertRaises(RuntimeError), core.maintenance():
            raise RuntimeError("fixture")
        self.assertTrue(self.reserve())

    def test_reused_live_pid_without_worker_lock_is_interrupted(self):
        operations.record("stop", "running", "Obsolete", pid=os.getpid())
        self.assertEqual(core.operation()["status"], "interrupted")
        self.assertFalse(core.busy())

    def test_worker_signals_are_restored_after_the_operation(self):
        before = signal.getsignal(signal.SIGTERM)
        operations.worker("stop", self.reserve())
        self.assertIs(signal.getsignal(signal.SIGTERM), before)


# A fake omarchy-vm: it lists one backup, otherwise waits for a release file
# and then writes output, as a real provider does after a worker may be gone.
FAKE_VM = """#!/usr/bin/python3
import json,os,sys,time
from pathlib import Path
marks=Path(%r)
if sys.argv[1:2]==['backups']:
    print(json.dumps([{'id':'omarchy-20261026T205001Z','verified':True,'createdAt':'2026-10-26T20:50:01Z'}]))
    raise SystemExit
(marks/'started').write_text(str(os.getpid()))
while not (marks/'release').exists():time.sleep(0.02)
for _ in range(3):print('Backup omarchy-20261027T000000Z saved and verified',flush=True)
(marks/'done').write_text(' '.join(sys.argv[1:]))
"""


class ProviderLifetimeTests(unittest.TestCase):
    # Real worker processes: the operation lock must last exactly as long as
    # the provider, whatever happens to the worker that started it.
    ACTIONS = {"backup": ({}, b""), "allocate": ({"cpu":1,"ram":4}, b""),
               "restore": ({"backupId":"omarchy-20261026T205001Z"}, b"RESTORE omarchy-20261026T205001Z\n")}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        home, self.marks, state = root/"home", root/"marks", root/"state"
        (home/".local/bin").mkdir(parents=True); self.marks.mkdir()
        (home/".config/omarchy").mkdir(parents=True)
        (home/".config/omarchy/shell.json").write_text(json.dumps({"bar":{"layout":{"right":[{"id":core.ID,"notify":False}]}}}))
        control = home/".local/bin/omarchy-vm"
        control.write_text(FAKE_VM % str(self.marks)); control.chmod(0o700)
        p = patch.dict(os.environ, {"XDG_STATE_HOME":str(state)}); p.start(); self.addCleanup(p.stop)
        self.env = {**os.environ, "HOME":str(home), "XDG_STATE_HOME":str(state)}
        self.addCleanup(self.release)

    def release(self):
        (self.marks/"release").touch()

    def start(self, action):
        for mark in self.marks.iterdir(): mark.unlink()
        parameters, typed = self.ACTIONS[action]
        started = time.time()
        core.write_json(core.state_dir()/"operation.json", {"action":action,"status":"starting","machineId":"omarchy",
                        "requestId":"fixture","startedEpoch":started,"expiresEpoch":started+120,"parameters":parameters})
        argv = [sys.executable,"-B",str(BACKEND/"omachinesctl"),"worker",action,"--request-id","fixture"]
        worker = subprocess.Popen(argv+(["--interactive"] if action=="restore" else []), env=self.env,
                                  stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: worker.poll() is None and (worker.kill(), worker.wait()))
        worker.stdin.write(typed); worker.stdin.close()
        self.wait_for(lambda: (self.marks/"started").exists())
        return worker

    def wait_for(self, condition, timeout=5):
        deadline = time.monotonic()+timeout
        while not condition():
            if time.monotonic() > deadline: self.fail("timed out")
            time.sleep(0.02)

    def assert_excluded(self):
        self.assertTrue(core.busy())
        with self.assertRaises(core.OperationError), core.maintenance():
            self.fail("admitted maintenance")

    def test_terminated_worker_waits_and_records_the_outcome(self):
        for action in self.ACTIONS:
            with self.subTest(action):
                worker = self.start(action)
                worker.terminate(); time.sleep(0.3)
                self.assertIsNone(worker.poll(), "worker abandoned its provider")
                self.assert_excluded()
                self.release()
                self.assertEqual(worker.wait(timeout=5), 128+signal.SIGTERM)
                self.assertTrue((self.marks/"done").exists())
                self.assertEqual(core.operation()["status"], "complete")
                self.assertFalse(core.busy())

    def test_killed_worker_leaves_the_lock_with_its_provider(self):
        for action in self.ACTIONS:
            with self.subTest(action):
                worker = self.start(action)
                worker.kill(); worker.wait(timeout=5)
                self.assert_excluded()
                op = core.operation()
                self.assertEqual(op["status"], "running")
                self.assertIn("result will not be reported", op["message"])
                self.release()
                # Its later output must not break the provider.
                self.wait_for(lambda: (self.marks/"done").exists())
                self.wait_for(lambda: not core.busy())
                self.assertEqual(core.operation()["status"], "interrupted")


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        p = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.path/"state")})
        p.start(); self.addCleanup(p.stop)
        self.work = self.path/"provider"
        (self.work/"research").mkdir(parents=True)
        (self.work/"create-development-backup.sh").write_text("# OMACHINES_PROGRESS_FD backup_copy_lock")
        self.old = self.path/"development-ready-20000101T000000Z"
        self.old.mkdir()
        self.new = self.path/"development-ready-20000102T000000Z"
        self.new.mkdir()
        self.config = {"setupWorkspace":str(self.work)}
        self.result(self.old)
        core.write_json(core.state_dir()/"backups.json", [{"path":str(self.old), "verified":True}])

    def result(self, path):
        (self.work/"research/development-ready.path").write_text(str(path))
        (self.work/"research/development-ready.status").write_text("complete")

    def provider(self, status=0, advance=True, interrupted=False):
        def lines():
            yield "OMACHINES_STAGE:copying\n"
            if interrupted:
                raise KeyboardInterrupt()
            if advance:
                self.result(self.new)
            yield "OMACHINES_STAGE:complete\n"
        return Mock(stdout=lines(), returncode=status)

    def test_provider_failure_does_not_claim_new_verified_backup(self):
        with patch("operations.subprocess.Popen", return_value=self.provider(status=1)):
            with self.assertRaises(core.OperationError): operations.backup(self.config)
        self.assertEqual(len(core.read_json(core.state_dir()/"backups.json")), 1)

    def test_unchanged_completion_record_is_rejected(self):
        with patch("operations.subprocess.Popen", return_value=self.provider(advance=False)):
            with self.assertRaises(core.OperationError): operations.backup(self.config)
        self.assertEqual(len(core.read_json(core.state_dir()/"backups.json")), 1)

    def test_interruption_retains_old_backup_without_new_verification(self):
        with patch("operations.subprocess.Popen", return_value=self.provider(interrupted=True)):
            with self.assertRaises(KeyboardInterrupt): operations.backup(self.config)
        self.assertEqual(len(core.read_json(core.state_dir()/"backups.json")), 1)

    def test_verified_backup_retains_history_and_checks_recovery(self):
        state = {"checks":{"ssh":{"status":"ready"}, "cua":{"status":"ready"}}}
        with patch("operations.subprocess.Popen", return_value=self.provider()), patch("operations.collect", return_value=state) as check:
            self.assertIn("Windows is ready", operations.backup(self.config))
        # Pinned to Windows: the panel may have switched to another machine.
        check.assert_called_once_with(self.config, deep=True, machine_id="windows")
        self.assertEqual(len(core.read_json(core.state_dir()/"backups.json")), 2)


if __name__ == "__main__": unittest.main()


class SupervisorTests(unittest.TestCase):
    def supervise(self, code, **kwargs):
        return subprocess.run([sys.executable, "-I", "-B", str(BACKEND/"supervise.py"), sys.executable, "-c", code],
                              timeout=10, **kwargs)

    def test_provider_exit_status_is_reported(self):
        self.assertEqual(self.supervise("raise SystemExit(3)", capture_output=True).returncode, 3)
        self.assertEqual(self.supervise("import os,signal; os.kill(os.getpid(),signal.SIGTERM)").returncode, 128+signal.SIGTERM)

    def test_terminal_output_is_left_to_the_provider(self):
        import pty
        master, terminal = pty.openpty()
        try:
            result = self.supervise("import os,sys; sys.exit(0 if os.isatty(1) else 1)", stdout=terminal)
        finally:
            os.close(master); os.close(terminal)
        self.assertEqual(result.returncode, 0)
