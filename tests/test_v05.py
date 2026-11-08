"""Version 0.5: Windows readiness, restart, updates and optional passwordless lifecycle.

Fixtures never run SSH, Docker, pkexec or sudo, and never touch a real VM.
"""
import sealed  # refuses a direct run on the desktop
import json
from pathlib import Path
import re
import signal
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"backend"))
import core, machines, operations, probes


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

    def test_stopping_forgets_the_banner_so_the_next_boot_shows_as_starting(self):
        core._ssh_seen = time.monotonic()
        self.addCleanup(core.forget_ssh)
        self.state(set(), False)
        self.assertIsNone(core._ssh_seen)

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


class SignalGuardTests(unittest.TestCase):
    """Patching a module's subprocess.Popen patches it everywhere, and a mocked
    pid converts to 1. killpg(1) is kill(-1): on 8 November 2026 a test did that
    and killed the whole desktop session. No signal is really sent here."""

    def test_only_a_real_childs_pid_is_signalled(self):
        with patch("os.killpg") as killpg:
            for pid in (MagicMock(), 1, 0, -1, True, None, "42", 42.0):
                with self.assertRaises(ProcessLookupError):
                    probes.signal_group(pid, signal.SIGTERM)
            killpg.assert_not_called()
            probes.signal_group(4242, signal.SIGTERM)
            killpg.assert_called_once_with(4242, signal.SIGTERM)

    def test_a_mocked_popen_cannot_touch_real_descriptors(self):
        with patch("subprocess.Popen", return_value=MagicMock()), patch("os.set_blocking") as blocking, \
                patch("os.killpg") as killpg, patch("os.kill") as kill:
            with self.assertRaises(probes.ProbeError):
                probes.Process(["anything"])
        blocking.assert_not_called()
        killpg.assert_not_called()
        kill.assert_not_called()
        probes._active.clear()

    def test_closing_a_mocked_process_signals_nothing(self):
        child = probes.Process.__new__(probes.Process)
        child._close_lock = threading.RLock()
        child.closed = False
        child.proc = MagicMock()
        with patch("os.killpg") as killpg, patch("os.kill") as kill:
            child.close()
        killpg.assert_not_called()
        kill.assert_not_called()
        self.assertTrue(child.closed)

    def test_no_backend_code_signals_groups_directly(self):
        for path in [*(ROOT/"backend").glob("*.py"), ROOT/"backend/omachinesctl", ROOT/"install.py"]:
            code = [line.partition("#")[0] for line in path.read_text().splitlines()]
            direct = [line for line in code if "killpg(" in line or "os.kill(" in line]
            self.assertEqual(direct, ["    os.killpg(pid, sig)"] if path.name == "probes.py" else [], path.name)


class FocusTests(unittest.TestCase):
    def calls(self, workspace):
        clients = json.dumps([{"address": "0xabc", "workspace": {"id": -99, "name": workspace}}])
        replies = {"clients": clients, "activeworkspace": json.dumps({"id": 7})}
        with patch("core.run", side_effect=lambda argv, timeout=15: replies.get(argv[1], "")) as run:
            core.focus_window("0xabc")
        return [c[0][0][-1] for c in run.call_args_list if c[0][0][1] == "dispatch"]

    def test_plain_window_is_only_focused(self):
        self.assertEqual(self.calls("3"), ['hl.dsp.focus({ window = "address:0xabc" })'])

    def test_window_minimized_to_the_dock_comes_back_to_this_workspace(self):
        self.assertEqual(self.calls("special:minimized"),
                         ['hl.dsp.window.move({ workspace = "7", window = "address:0xabc" })',
                          'hl.dsp.focus({ window = "address:0xabc" })'])


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
    def restart(self, banners, boots, viewers=()):
        banners, boots = iter(banners), iter(boots)
        with patch("operations.ssh_banner", side_effect=lambda: next(banners)), patch("operations.guest_boot", side_effect=lambda: next(boots)), \
                patch("operations.run") as self.run_call, patch("operations.viewer_windows", return_value=list(viewers)), \
                patch("operations.subprocess.Popen") as self.launch:
            return operations.restart_windows()

    def test_waits_for_a_new_start_time(self):
        self.assertEqual(self.restart([True, True, False, True], ["A", "A", "B"]), "Windows restarted")
        self.assertIn("shutdown.exe /r", self.run_call.call_args[0][0][1])
        self.assertIn("Windows is restarting", self.records)
        self.launch.assert_not_called()

    def test_one_missed_probe_is_not_a_restart(self):
        # The guest misses a probe, then still answers with its old start time.
        self.assertEqual(self.restart([True, False, True, True], ["A", "A", "B"]), "Windows restarted")

    def test_an_open_viewer_is_reopened_once_windows_is_back(self):
        self.assertEqual(self.restart([True, False, True], ["A", "B"], ["0x1"]), "Windows restarted; viewer reopened")
        self.assertEqual(self.launch.call_args[0][0], ["/fixture/windows-vm-launch"])

    def test_refused_when_ssh_does_not_answer(self):
        with self.assertRaisesRegex(core.OperationError, "not answering over SSH"):
            self.restart([False], [])
        self.run_call.assert_not_called()

    def test_refused_without_a_start_time_to_compare(self):
        with self.assertRaisesRegex(core.OperationError, "nothing was restarted"):
            self.restart([True], [""])
        self.run_call.assert_not_called()

    def test_a_blocked_restart_is_reported(self):
        clock = iter(range(0, 100000, 1000))
        with patch("operations.time.monotonic", side_effect=lambda: next(clock)), self.assertRaisesRegex(core.OperationError, "did not begin restarting"):
            self.restart(iter(lambda: True, None), iter(lambda: "A", None))

    def test_windows_that_never_returns_is_reported(self):
        clock = iter(range(0, 100000, 1000))
        def banners():
            yield True
            while True:
                yield False
        with patch("operations.time.monotonic", side_effect=lambda: next(clock)), self.assertRaisesRegex(core.OperationError, "did not come back"):
            self.restart(banners(), ["A"])

    def test_stop_request_leaves_the_restart_running_and_does_not_claim_success(self):
        def banners():
            yield True
            operations.stopping.append(15)
            while True:
                yield True
        with self.assertRaisesRegex(core.OperationError, "stopped following"):
            self.restart(banners(), ["A", "A"])

    def test_guest_boot_rejects_anything_but_a_timestamp(self):
        with patch("operations.guest_powershell", return_value="<b>soon</b>"):
            self.assertEqual(operations.guest_boot(), "")
        with patch("operations.guest_powershell", side_effect=core.OperationError("down")):
            self.assertEqual(operations.guest_boot(), "")
        with patch("operations.guest_powershell", return_value="2026-11-08T13:04:47.5+00:00"):
            self.assertEqual(operations.guest_boot(), "2026-11-08T13:04:47.5+00:00")


class UpdateTests(Worker):
    RUNNING = {"status": "running", "stage": "apps"}

    def reads(self, rows):
        script = iter(rows)
        self.removed = []

        def read(code, timeout=18):
            if "Register-ScheduledTask" in code:
                return ""
            if "Unregister-ScheduledTask" in code:
                self.removed.append(True)
                return ""
            value = next(script)
            if isinstance(value, Exception):
                raise value
            task, body = value if isinstance(value, tuple) else ("Running", value)
            return f"task={task}\n" + (json.dumps({**body, "runId": "fixed"}) if isinstance(body, dict) else body)
        return read

    def run_update(self, *rows):
        with patch("operations.copy_to_guest") as copied, patch("operations.guest_powershell", side_effect=self.reads(rows)), \
                patch("operations.uuid.uuid4", return_value=MagicMock(hex="fixed"+"0"*27)), \
                patch("operations.parse_update", side_effect=lambda text, _: PARSE(text, "fixed")):
            try:
                return operations.update_windows()
            finally:
                copied.assert_called_once()

    def test_stages_are_reported_and_counts_summarised(self):
        done = {"status": "complete", "stage": "finished", "windowsUpdates": 1, "apps": 8, "rebootRequired": True, "problems": []}
        result = self.run_update(self.RUNNING, core.OperationError("blip"), self.RUNNING, done)
        self.assertEqual(result, "Windows and tools updated: 1 Windows update, 8 apps; restart Windows to finish")
        self.assertIn(operations.UPDATE_STAGES["apps"], self.records)
        self.assertTrue(self.removed, "the elevated task must not outlive the run")

    def test_problems_never_read_as_updated_and_are_not_quoted(self):
        result = self.run_update({"status": "partial", "windowsFailed": 1, "appsFailed": 1, "problems": ["secret text"]})
        self.assertTrue(result.startswith("Update finished with problems"), result)
        self.assertIn("3 items did not update", result)
        self.assertNotIn("secret text", result)

    def test_guest_failure_raises(self):
        with self.assertRaisesRegex(core.OperationError, "stopped early"):
            self.run_update({"status": "failed"})
        self.assertTrue(self.removed)

    def test_stop_request_leaves_the_update_running_and_does_not_claim_success(self):
        operations.stopping.append(15)
        with self.assertRaisesRegex(core.OperationError, "continues in Windows"):
            self.run_update()
        self.assertFalse(self.removed, "a task still working must not be removed")

    def test_a_task_that_died_releases_the_panel_at_once(self):
        # A crash or power cut leaves "running" in the file with no live task.
        with self.assertRaisesRegex(core.OperationError, "stopped before it finished"):
            self.run_update(("Ready", self.RUNNING), ("Ready", self.RUNNING))
        self.assertTrue(self.removed)

    def test_one_odd_task_state_is_not_enough(self):
        done = {"status": "complete", "windowsUpdates": 0, "apps": 0}
        self.assertIn("updated", self.run_update(("Ready", self.RUNNING), self.RUNNING, ("Ready", done)))

    def test_a_task_that_never_starts_is_reported(self):
        with self.assertRaisesRegex(core.OperationError, "signed in"):
            self.run_update(("Ready", ""), ("Ready", ""))

    def test_a_silent_guest_releases_the_panel(self):
        clock = iter(range(0, 10000000, 700))
        with patch("operations.time.monotonic", side_effect=lambda: next(clock)), \
                self.assertRaisesRegex(core.OperationError, "stopped answering"):
            self.run_update(*[core.OperationError("down")]*20)
        self.assertFalse(self.removed)

    def test_old_or_foreign_results_are_ignored(self):
        self.assertIsNone(operations.parse_update(json.dumps({"runId": "other", "status": "complete"}), "mine"))
        self.assertIsNone(operations.parse_update("not json", "mine"))
        parsed = operations.parse_update(json.dumps({"runId": "mine", "status": "running", "stage": "<b>x"}), "mine")
        self.assertEqual(parsed["stage"], "")

    def test_script_folder_is_locked_before_the_script_arrives(self):
        text = operations.UPDATE_INSTALL
        self.assertLess(text.index("/inheritance:r"), text.index("[IO.File]::Create"))
        self.assertNotIn("S-1-5-11", text)  # Authenticated Users

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
