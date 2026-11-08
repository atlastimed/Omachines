"""Version 0.5: Windows readiness, restart, updates and optional passwordless lifecycle.

Fixtures never run SSH, Docker, pkexec or sudo, and never touch a real VM.
"""
import json
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"backend"))
import core, machines, operations


PARSE = operations.parse_update


class Listener:
    """A loopback port like Docker's proxy (silent) or a guest's sshd (banner)."""
    def __init__(self, banner=b""):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.banner = banner
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            if self.banner:
                client.sendall(self.banner)
            time.sleep(0.05)
            client.close()

    def close(self):
        self.sock.close()


class BannerTests(unittest.TestCase):
    def setUp(self):
        core._ssh_seen = None
        self.addCleanup(setattr, core, "_ssh_seen", None)

    def test_docker_proxy_alone_is_not_ssh(self):
        proxy = Listener()
        self.addCleanup(proxy.close)
        self.assertTrue(core.tcp(proxy.port))
        self.assertFalse(core.ssh_banner(proxy.port))

    def test_sshd_banner_counts(self):
        sshd = Listener(b"SSH-2.0-OpenSSH_for_Windows_9.5\r\n")
        self.addCleanup(sshd.close)
        self.assertTrue(core.ssh_banner(sshd.port))

    def test_closed_port_is_not_ssh(self):
        probe = socket.create_server(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        self.assertFalse(core.ssh_banner(port))

    def test_a_seen_banner_is_trusted_only_for_a_while_and_only_while_the_port_is_open(self):
        with patch("core.tcp", return_value=True), patch("core.ssh_banner", return_value=True) as banner:
            self.assertTrue(core.ssh_answering())
            self.assertTrue(core.ssh_answering())
            banner.assert_called_once()
            core._ssh_seen = time.monotonic()-core.SSH_TRUST-1
            self.assertTrue(core.ssh_answering())
            self.assertEqual(banner.call_count, 2)
        with patch("core.tcp", return_value=False), patch("core.ssh_banner", return_value=False) as banner:
            self.assertFalse(core.ssh_answering())
            banner.assert_called_once()
            self.assertIsNone(core._ssh_seen)


class ReachabilityTests(unittest.TestCase):
    def state(self, open_ports, ssh, op=None):
        with patch("core.tcp", side_effect=lambda port: port in open_ports), patch("core.ssh_answering", return_value=ssh), \
                patch("core.last_operation", side_effect=lambda value, _: value):
            return core.windows_reachability(op or {})

    def test_booting_windows_is_starting_although_its_ports_answer(self):
        self.assertEqual(self.state({2222, 8006}, False), ("starting", "Windows is starting"))

    def test_running_needs_the_guests_own_answer(self):
        self.assertEqual(self.state({2222, 8006}, True), ("running", "Windows is reachable"))

    def test_no_published_ports_means_stopped(self):
        self.assertEqual(self.state(set(), False), ("stopped", "Windows is stopped"))

    def test_backup_keeps_its_own_summary(self):
        self.assertEqual(self.state(set(), False, {"action": "backup", "status": "running"}), ("unreachable", "Backup in progress"))


class GuestFieldTests(unittest.TestCase):
    def test_version_and_update_fields_are_reduced_to_known_shapes(self):
        guest = core.parse_guest({"osVersion": "26H2 build 26300.9711", "rebootPending": True, "updateStatus": "complete", "updatedAt": "2026-11-08T12:00:00.0Z"})
        self.assertEqual((guest["osVersion"], guest["rebootPending"], guest["updateStatus"]), ("26H2 build 26300.9711", True, "complete"))
        self.assertEqual(guest["updatedAt"], "2026-11-08T12:00:00.0Z")

    def test_markup_and_unknown_values_are_dropped(self):
        guest = core.parse_guest({"osVersion": "<img src=x> build 1.1", "rebootPending": "yes", "updateStatus": "pwned", "updatedAt": "soon <b>"})
        self.assertEqual((guest["osVersion"], guest["rebootPending"], guest["updateStatus"], guest["updatedAt"]), ("", False, "", ""))


class QuickLifecycleTests(unittest.TestCase):
    machine = machines.windows_machine()

    def test_terminal_is_needed_without_the_rule(self):
        with patch("operations.quick_lifecycle", return_value=False):
            self.assertTrue(operations.terminal("start", self.machine))
            self.assertTrue(operations.terminal("stop", self.machine))

    def test_rule_removes_the_terminal_only_for_start_and_stop(self):
        with patch("operations.quick_lifecycle", return_value=True):
            self.assertFalse(operations.terminal("start", self.machine))
            self.assertFalse(operations.terminal("stop", self.machine))
            for action in ("backup", "restore", "allocate", "rollback"):
                self.assertTrue(operations.terminal(action, self.machine), action)

    def run_priv(self, quick):
        calls = []

        class Provider:
            def __init__(self, argv, **options): calls.append((argv, options))
            def __enter__(self): return MagicMock(returncode=0)
            def __exit__(self, *_): return False
        with patch("operations.quick_lifecycle", return_value=quick), patch("operations.provider", Provider):
            operations.vm_priv("up")
        return calls[0]

    def test_pkexec_runs_detached_with_the_exact_allowed_command(self):
        argv, options = self.run_priv(True)
        self.assertEqual(argv, ["/usr/bin/pkexec", "/usr/bin/omarchy-windows-vm", "__priv", "up"])
        self.assertTrue(options["detach"])

    def test_without_the_rule_sudo_keeps_the_visible_terminal(self):
        argv, options = self.run_priv(False)
        self.assertEqual(argv[0], "sudo")
        self.assertEqual(options, {})

    def test_marker_must_be_a_root_owned_regular_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            mine = Path(tmp)/"marker"
            mine.write_text("x")
            with patch("core.QUICK_MARKER", mine):
                self.assertFalse(core.quick_lifecycle())
            with patch("core.QUICK_MARKER", Path(tmp)/"missing"):
                self.assertFalse(core.quick_lifecycle())


class RuleTests(unittest.TestCase):
    source = (ROOT/"scripts/install-admin.py").read_text()

    def test_rule_allows_only_fixed_commands(self):
        self.assertIn("ALLOWED=('up','up_wait','down')", self.source)
        for word in ("write_compose", "remove", "status"):
            self.assertNotIn(f"'{word}'", self.source.split("ALLOWED=")[1].split("\n")[0])
        self.assertIn('subject.active', self.source)
        self.assertIn('action.lookup("command_line")', self.source)

    def test_shutdown_dropin_outlasts_the_containers_grace_period(self):
        timeout = int(re.search(r"\[Service\]\\nTimeoutStopSec=(\d+)", self.source)[1])
        self.assertGreater(timeout, 120+5)


class Worker(unittest.TestCase):
    def setUp(self):
        operations.stopping.clear()
        self.records = []
        for target, value in (("operations.record", lambda *a, **k: self.records.append(a[2])),
                              ("operations.wrapper", lambda name: "/fixture/"+name),
                              ("operations.update_poll", lambda: 0), ("operations.restart_poll", lambda: 0)):
            starter = patch(target, value)
            starter.start()
            self.addCleanup(starter.stop)


class RestartTests(Worker):
    def test_waits_for_windows_to_go_and_come_back(self):
        answers = iter([True, True, False, False, True])
        with patch("operations.ssh_banner", side_effect=lambda: next(answers)), patch("operations.run") as run:
            self.assertEqual(operations.restart_windows(), "Windows restarted")
        self.assertIn("shutdown.exe /r", run.call_args[0][0][1])
        self.assertIn("Windows is restarting", self.records)

    def test_refused_when_ssh_does_not_answer(self):
        with patch("operations.ssh_banner", return_value=False), patch("operations.run") as run:
            with self.assertRaisesRegex(core.OperationError, "not answering over SSH"):
                operations.restart_windows()
        run.assert_not_called()

    def test_a_blocked_restart_is_reported(self):
        clock = iter(range(0, 100000, 1000))
        with patch("operations.ssh_banner", return_value=True), patch("operations.run"), \
                patch("operations.time.monotonic", side_effect=lambda: next(clock)):
            with self.assertRaisesRegex(core.OperationError, "did not begin restarting"):
                operations.restart_windows()

    def test_stop_request_leaves_the_restart_running(self):
        def banner():
            operations.stopping.append(15)
            return True
        with patch("operations.ssh_banner", side_effect=banner), patch("operations.run"):
            self.assertIn("stopped following", operations.restart_windows())


class UpdateTests(Worker):
    def reads(self, *rows):
        script = iter(rows)

        def read(code, timeout=18):
            if "Register-ScheduledTask" in code:
                return ""
            value = next(script)
            if isinstance(value, Exception):
                raise value
            return json.dumps({**value, "runId": "fixed"}) if isinstance(value, dict) else value
        return read

    def run_update(self, *rows):
        with patch("operations.copy_to_guest") as copied, patch("operations.guest_powershell", side_effect=self.reads(*rows)), \
                patch("operations.uuid.uuid4", return_value=MagicMock(hex="fixed"+"0"*27)), \
                patch("operations.parse_update", side_effect=lambda text, _: PARSE(text, "fixed")):
            result = operations.update_windows()
        copied.assert_called_once()
        return result

    def test_stages_are_reported_and_counts_summarised(self):
        running = {"status": "running", "stage": "apps"}
        done = {"status": "complete", "stage": "finished", "windowsUpdates": 1, "apps": 8, "rebootRequired": True, "problems": []}
        result = self.run_update(running, core.OperationError("blip"), running, done)
        self.assertEqual(result, "Windows and tools updated: 1 Windows update, 8 apps; restart Windows to finish")
        self.assertIn(operations.UPDATE_STAGES["apps"], self.records)

    def test_failed_items_point_at_the_guest_log_without_quoting_it(self):
        done = {"status": "complete", "windowsFailed": 1, "appsFailed": 1, "problems": ["secret text"]}
        result = self.run_update(done)
        self.assertIn("3 items did not update", result)
        self.assertNotIn("secret text", result)

    def test_guest_failure_raises(self):
        with self.assertRaisesRegex(core.OperationError, "stopped early"):
            self.run_update({"status": "failed"})

    def test_stop_request_leaves_the_update_running(self):
        operations.stopping.append(15)
        self.assertIn("continues in Windows", self.run_update())

    def test_a_task_that_never_starts_is_reported(self):
        clock = iter(range(0, 100000, 100))
        with patch("operations.copy_to_guest"), patch("operations.guest_powershell", return_value=""), \
                patch("operations.time.monotonic", side_effect=lambda: next(clock)):
            with self.assertRaisesRegex(core.OperationError, "sign in to Windows"):
                operations.update_windows()

    def test_old_or_foreign_results_are_ignored(self):
        self.assertIsNone(operations.parse_update(json.dumps({"runId": "other", "status": "complete"}), "mine"))
        self.assertIsNone(operations.parse_update("not json", "mine"))
        parsed = operations.parse_update(json.dumps({"runId": "mine", "status": "running", "stage": "<b>x"}), "mine")
        self.assertEqual(parsed["stage"], "")

    def test_script_is_ascii_for_the_guests_default_encoding(self):
        (ROOT/"backend/guest-maintain.ps1").read_bytes().decode("ascii")


class ActionTests(unittest.TestCase):
    def test_new_actions_need_confirmation_and_stay_windows_only(self):
        for action in ("restart", "update"):
            self.assertIn(action, operations.DISRUPTIVE)
            self.assertTrue(operations.supported(action, machines.windows_machine()))
            self.assertFalse(operations.supported(action, machines.omarchy_machine()))
            self.assertFalse(operations.terminal(action, machines.windows_machine()))


if __name__ == "__main__":
    unittest.main()
