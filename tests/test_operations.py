import fcntl
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import ANY, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"backend"))
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
        check.assert_called_once_with(self.config, deep=True)
        self.assertEqual(len(core.read_json(core.state_dir()/"backups.json")), 2)


if __name__ == "__main__": unittest.main()
