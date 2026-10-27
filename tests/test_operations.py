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
(marks/'session').write_text(str(os.getsid(0))+' '+str(os.getppid()))
(marks/'started').write_text(str(os.getpid()))
# Never outlive the test: give up when its folder goes or after a minute.
deadline=time.monotonic()+60
while not (marks/'release').exists():
    if not marks.is_dir() or time.monotonic()>deadline:raise SystemExit(1)
    time.sleep(0.02)
for _ in range(3):print('Backup omarchy-20261027T000000Z saved and verified',flush=True)
(marks/'done').write_text(' '.join(sys.argv[1:]))
"""
# Runs omachinesctl on a fixed 32 GiB host, so the allocation capacity check
# does not depend on the test runner's memory.
FIXED_HOST = """import os,runpy,sys
real=os.sysconf
os.sysconf=lambda name: 32*1024**3//real('SC_PAGE_SIZE') if name=='SC_PHYS_PAGES' else real(name)
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
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
        argv = [sys.executable,"-B","-c",FIXED_HOST,str(BACKEND/"omachinesctl"),"worker",action,"--request-id","fixture"]
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

    def test_provider_runs_in_its_own_session(self):
        # The operation terminal's hangup, termination and Ctrl+C reach the
        # worker's session only; the supervisor and omarchy-vm never see them.
        for action in self.ACTIONS:
            with self.subTest(action):
                worker = self.start(action)
                session, supervisor = map(int, (self.marks/"session").read_text().split())
                self.assertNotEqual(session, os.getsid(worker.pid))
                self.assertEqual(session, supervisor, "the supervisor should lead the provider's new session")
                self.release()
                self.assertEqual(worker.wait(timeout=5), 0)
                self.assertEqual(core.operation()["status"], "complete")

    def test_confirmation_wait_is_cancelled_at_once(self):
        # No provider runs while restore waits for its typed confirmation, so
        # Ctrl+C or termination ends the request instead of holding the lock.
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(sig):
                parameters, _ = self.ACTIONS["restore"]
                started = time.time()
                core.write_json(core.state_dir()/"operation.json", {"action":"restore","status":"starting","machineId":"omarchy",
                                "requestId":"fixture","startedEpoch":started,"expiresEpoch":started+120,"parameters":parameters})
                argv = [sys.executable,"-B","-c",FIXED_HOST,str(BACKEND/"omachinesctl"),"worker","restore","--request-id","fixture","--interactive"]
                worker = subprocess.Popen(argv, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.addCleanup(lambda: worker.poll() is None and (worker.kill(), worker.wait()))
                self.wait_for(lambda: core.operation().get("message") == "Waiting for typed confirmation")
                worker.send_signal(sig)
                self.assertEqual(worker.wait(timeout=5), 128+sig)
                worker.stdin.close()
                self.assertFalse(core.busy())
                self.assertEqual(core.operation()["status"], "failed")
                self.assertEqual(core.operation()["message"], "Cancelled; nothing was changed")
                self.assertFalse((self.marks/"started").exists(), "the provider ran")

    def test_failure_message_is_read_without_holding_the_lock(self):
        parameters, _ = self.ACTIONS["restore"]
        started = time.time()
        core.write_json(core.state_dir()/"operation.json", {"action":"restore","status":"starting","machineId":"omarchy",
                        "requestId":"fixture","startedEpoch":started,"expiresEpoch":started+120,"parameters":parameters})
        argv = [sys.executable,"-B","-c",FIXED_HOST,str(BACKEND/"omachinesctl"),"worker","restore","--request-id","fixture","--interactive"]
        worker = subprocess.Popen(argv, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: worker.poll() is None and (worker.kill(), worker.wait()))
        worker.stdin.write(b"no\n"); worker.stdin.flush()
        self.wait_for(lambda: core.operation().get("status") == "failed")
        self.wait_for(lambda: not core.busy())
        self.assertIsNone(worker.poll(), "the terminal closed before the message was read")
        worker.send_signal(signal.SIGINT)
        self.assertEqual(worker.wait(timeout=5), 1)
        self.assertEqual(core.operation()["message"], "Restore cancelled; Omarchy VM is unchanged")
        self.assertFalse((self.marks/"started").exists())

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


class ProviderSessionTests(unittest.TestCase):
    def popen(self):
        return patch("operations.subprocess.Popen", return_value=Mock(returncode=0, stdout=None))

    def test_only_detached_providers_get_a_new_session(self):
        with self.popen() as popen, operations.provider(["/usr/bin/true"], detach=True):
            pass
        self.assertIs(popen.call_args.kwargs["start_new_session"], True)
        with self.popen() as popen, operations.provider(["/usr/bin/true"]):
            pass
        self.assertNotIn("start_new_session", popen.call_args.kwargs)

    def test_windows_sudo_providers_keep_the_terminal(self):
        with self.popen() as popen:
            operations.vm_priv("up")
        self.assertEqual(popen.call_args.args[0][0], "sudo")
        self.assertNotIn("start_new_session", popen.call_args.kwargs)
        helper = Mock(); helper.is_file.return_value = True; helper.is_symlink.return_value = False
        helper.stat.return_value = Mock(st_uid=0, st_mode=0o100755)
        with self.popen() as popen, patch("operations.Path", return_value=helper):
            popen.return_value.stdout = io.StringIO("Done\n")
            operations.admin_action("verify", {"backupId": "omachines-20260101T000000Z-abcdef"})
        self.assertEqual(popen.call_args.args[0][0], "sudo")
        self.assertNotIn("start_new_session", popen.call_args.kwargs)


# A fake windows-vm-launch: it waits (like an authorization prompt), then
# either opens its "viewer" or exits with the requested status, and records
# whether it ever got to start Windows.
FAKE_LAUNCHER = """#!/usr/bin/python3
import sys,time
from pathlib import Path
marks=Path(%r)
(marks/'started').touch()
deadline=time.monotonic()+60
while not (marks/'approve').exists():
    if not marks.is_dir() or time.monotonic()>deadline:raise SystemExit(1)
    time.sleep(0.02)
code=int((marks/'approve').read_text() or 0)
if code:raise SystemExit(code)
(marks/'windows-started').touch()
(marks/'viewer').touch()
while not (marks/'close').exists() and marks.is_dir() and time.monotonic()<deadline:time.sleep(0.02)
(marks/'closed').touch()
"""


class WindowsOpenTests(unittest.TestCase):
    # The real worker and supervisor: an open request keeps the operation lock
    # until its viewer is up, so an old authorization prompt approved later
    # can never start Windows after another operation.
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name); self.marks = root/"marks"; self.marks.mkdir()
        p = patch.dict(os.environ, {"XDG_STATE_HOME": str(root/"state")}); p.start(); self.addCleanup(p.stop)
        launcher = root/"windows-vm-launch"; launcher.write_text(FAKE_LAUNCHER % str(self.marks)); launcher.chmod(0o700)
        config = {"demo": False, "notify": False, "asideEnabled": False, "pollSeconds": 120, "setupWorkspace": ""}
        for target, value in (("operations.settings", config), ("operations.wrapper", str(launcher)), ("operations.ensure_memory", None)):
            p = patch(target, return_value=value); p.start(); self.addCleanup(p.stop)
        p = patch("operations.viewer_windows", side_effect=lambda: ["0x1"] if (self.marks/"viewer").exists() else []); p.start(); self.addCleanup(p.stop)
        self.addCleanup(lambda: (self.marks/"close").touch())
        started = time.time()
        core.write_json(core.state_dir()/"operation.json", {"action":"open","status":"starting","machineId":"windows","requestId":"fixture",
                        "startedEpoch":started,"expiresEpoch":started+120,"parameters":{}})

    def wait_for(self, condition, timeout=5):
        deadline = time.monotonic()+timeout
        while not condition():
            if time.monotonic() > deadline: return False
            time.sleep(0.02)
        return True

    def run_worker(self, while_pending):
        # The worker installs signal handlers, so it runs on this thread while
        # another one inspects the pending state and then answers the prompt.
        seen = {}
        def inspect():
            if self.wait_for(lambda: (self.marks/"started").exists()):
                seen["busy"] = core.busy()
                try:
                    with core.maintenance(): seen["maintenance"] = True
                except core.OperationError:
                    seen["maintenance"] = False
                while_pending()
        helper = threading.Thread(target=inspect); helper.start()
        with redirect_stdout(io.StringIO()):
            code = operations.worker("open", "fixture")
        helper.join(5)
        return code, seen

    def test_pending_authorization_keeps_the_lock_until_the_viewer_opens(self):
        code, seen = self.run_worker(lambda: (self.marks/"approve").write_text("0"))
        self.assertEqual((code, seen), (0, {"busy": True, "maintenance": False}))
        self.assertEqual(core.operation()["status"], "complete")
        self.assertEqual(core.operation()["message"], "Windows viewer opened")
        # The viewer outlives the operation, which no longer holds the lock.
        self.assertFalse(core.busy())
        self.assertFalse((self.marks/"closed").exists())
        (self.marks/"close").touch()
        self.assertTrue(self.wait_for(lambda: (self.marks/"closed").exists()))

    def test_cancelled_authorization_fails_and_releases_the_lock(self):
        code, seen = self.run_worker(lambda: (self.marks/"approve").write_text("1"))
        self.assertEqual((code, seen["busy"]), (1, True))
        self.assertEqual(core.operation()["status"], "failed")
        self.assertIn("authorization was cancelled", core.operation()["message"])
        self.assertFalse(core.busy())

    def test_unanswered_prompt_is_withdrawn_and_cannot_start_windows_later(self):
        with patch("operations.OPEN_WAIT", 0.5):
            code, seen = self.run_worker(lambda: None)
        self.assertEqual((code, seen["busy"]), (1, True))
        self.assertIn("request was withdrawn", core.operation()["message"])
        self.assertFalse(core.busy())
        (self.marks/"approve").write_text("0")
        time.sleep(0.3)
        self.assertFalse((self.marks/"windows-started").exists(), "a withdrawn request started Windows")


class SupervisorTests(unittest.TestCase):
    def supervise(self, code, **kwargs):
        return subprocess.run([sys.executable, "-I", "-B", str(BACKEND/"supervise.py"), sys.executable, "-c", code],
                              timeout=10, **kwargs)

    def test_provider_exit_status_is_reported(self):
        self.assertEqual(self.supervise("raise SystemExit(3)", capture_output=True).returncode, 3)
        self.assertEqual(self.supervise("import os,signal; os.kill(os.getpid(),signal.SIGTERM)").returncode, 128+signal.SIGTERM)

    def test_release_leaves_the_provider_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            mark = Path(tmp)/"alive"
            code = "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); p.touch(); time.sleep(1); p.write_text('done')"
            supervisor = subprocess.Popen([sys.executable, "-I", "-B", str(BACKEND/"supervise.py"), "--until-released",
                                           sys.executable, "-c", code, str(mark)])
            deadline = time.monotonic()+5
            while not mark.exists() and time.monotonic() < deadline: time.sleep(0.02)
            supervisor.send_signal(signal.SIGUSR1)
            self.assertEqual(supervisor.wait(timeout=5), 0)
            self.assertEqual(mark.read_text(), "")
            deadline = time.monotonic()+5
            while mark.read_text() != "done" and time.monotonic() < deadline: time.sleep(0.05)
            self.assertEqual(mark.read_text(), "done")
        # Without a release it reports the provider's own exit status.
        self.assertEqual(subprocess.run([sys.executable, "-I", "-B", str(BACKEND/"supervise.py"), "--until-released",
                                         sys.executable, "-c", "raise SystemExit(4)"], timeout=10).returncode, 4)

    def test_terminal_output_is_left_to_the_provider(self):
        import pty
        master, terminal = pty.openpty()
        try:
            result = self.supervise("import os,sys; sys.exit(0 if os.isatty(1) else 1)", stdout=terminal)
        finally:
            os.close(master); os.close(terminal)
        self.assertEqual(result.returncode, 0)
