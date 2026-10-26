"""Version 0.3: machine registry and Omarchy VM provider.

Fixtures never run omarchy-vm, SSH, sudo or Docker, and never touch a real VM.
"""
from contextlib import redirect_stdout
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"backend"))
import core, history, machines, omarchy, operations

# Real output shapes observed read-only from a running Omarchy VM.
STATUS = {"running": True, "guest": "running", "sshReady": True, "viewerOpen": False, "installMedia": "", "diskUsedBytes": 10457452544}
BACKUPS = [{"label": "Fresh install", "createdAt": "2026-10-26T20:50:40.461946+00:00", "bytes": 10457149440,
            "sha256": "0"*64, "verified": True, "verifiedAt": "2026-10-26T20:50:40.461980+00:00", "restoreTested": True,
            "restoreTestedAt": "2026-10-26T20:53:43.780846+00:00", "restoreEvidence": "private detail",
            "id": "omarchy-20261026T205001Z", "path": "/home/example/Omarchy-VM-Backups/omarchy-20261026T205001Z"}]
PROBE = "cpu=6\nmemoryKiB=8113128\ndisk=109519568896\nfree=90137178112\ndesktop=available\n"
HEALTH = {"structuredContent": {"schema_version": "1", "overall": "ok", "platform": "linux", "checks": [
    {"name": "session_active", "status": "pass", "message": "MCP session is active."},
    {"name": "ax_capability", "status": "pass", "message": "AT-SPI inspection will work."}]}}


def fake_run(status=STATUS, probe=PROBE, backups=BACKUPS, resources=None):
    def run(argv, timeout):
        if argv[1:] == ["status", "--json"]:
            if isinstance(status, Exception): raise status
            return json.dumps(status)
        if argv[1:] == ["backups", "--json"]: return json.dumps(backups)
        if argv[1:] == ["resources"]: return json.dumps(resources or {"cpus": 6, "memory": "8G", "hostCpus": 16})
        if argv[0].endswith("omarchy-vm-ssh"):
            if isinstance(probe, Exception): raise probe
            return probe
        raise AssertionError("Unexpected command " + repr(argv))
    return run


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.patch(patch.dict(os.environ, {"XDG_STATE_HOME": str(self.path)}))
        self.omarchy_installed = True
        self.patch(patch("machines.available", lambda m: m["provider"] == "omarchy-windows" or self.omarchy_installed))
        self.patch(patch("omarchy.executable", side_effect=lambda p: str(p)))
        self.registry = core.state_dir()/"machines.json"

    def patch(self, p):
        value = p.start(); self.addCleanup(p.stop); return value


class RegistryTests(Base):
    def v1(self, **changes):
        machine = {**machines.windows_machine(), **changes}
        self.registry.write_text(json.dumps({"schemaVersion": 1, "selected": "windows", "machines": [machine]}))
        return machine

    def test_v1_migration_keeps_windows_entry_and_original_file(self):
        old = self.v1(name="My Windows", note="kept")
        original = self.registry.read_text()
        data = machines.initialize()
        self.assertEqual(data["schemaVersion"], 2)
        self.assertEqual(data["selected"], "windows")
        self.assertEqual(data["machines"][0], old)
        self.assertEqual(data["machines"][1], machines.omarchy_machine())
        self.assertEqual(json.loads(self.registry.read_text()), data)
        self.assertEqual(json.loads((core.state_dir()/"machines-v1.json").read_text()), json.loads(original))
        before = self.registry.stat().st_mtime_ns
        self.assertEqual(machines.initialize(), data)
        self.assertEqual(self.registry.stat().st_mtime_ns, before)

    def test_missing_registry_defaults_to_both_providers(self):
        data = machines.load()
        self.assertEqual([m["id"] for m in data["machines"]], ["windows", "omarchy"])
        self.assertEqual(machines.selected(data)["id"], "windows")

    def test_v2_without_omarchy_entry_gains_default(self):
        self.registry.write_text(json.dumps({"schemaVersion": 2, "selected": "windows", "machines": [machines.windows_machine()]}))
        self.assertEqual(machines.load()["machines"][1]["id"], "omarchy")

    def test_custom_paths_and_unknown_shapes_fail_closed_and_preserve_file(self):
        bad_omarchy = {**machines.omarchy_machine(), "connections": {**machines.omarchy_machine()["connections"], "control": "/tmp/other"}}
        cases = [
            {"schemaVersion": 2, "selected": "windows", "machines": [machines.windows_machine(), bad_omarchy]},
            {"schemaVersion": 2, "selected": "windows", "machines": [machines.windows_machine(), {**machines.omarchy_machine(), "sshPort": 22}]},
            {"schemaVersion": 2, "selected": "windows", "machines": [machines.windows_machine(), machines.windows_machine()]},
            {"schemaVersion": 2, "selected": "other", "machines": [machines.windows_machine()]},
            {"schemaVersion": 2, "selected": "windows", "machines": [{**machines.omarchy_machine(), "id": "windows"}]},
            {"schemaVersion": 2, "selected": "omarchy", "machines": [{**machines.omarchy_machine(), "provider": "qemu-anything"}]},
            {"schemaVersion": 3, "selected": "windows", "machines": [machines.windows_machine()]},
            {"schemaVersion": 1, "selected": "omarchy", "machines": [machines.windows_machine()]},
        ]
        for value in cases:
            with self.subTest(value=value):
                self.registry.write_text(json.dumps(value))
                with self.assertRaises(ValueError): machines.initialize()
                self.assertEqual(json.loads(self.registry.read_text()), value)
                with patch("core.tcp", side_effect=AssertionError("no probes")):
                    self.assertEqual(core.collect(core.settings())["state"], "unknown")

    def test_selection_persists_and_requires_installed_control(self):
        machines.initialize()
        self.omarchy_installed = False
        with self.assertRaises(ValueError): machines.select("omarchy")
        self.assertEqual(machines.load()["selected"], "windows")
        self.omarchy_installed = True
        machines.select("omarchy")
        self.assertEqual(json.loads(self.registry.read_text())["selected"], "omarchy")
        self.assertEqual(machines.selected()["id"], "omarchy")
        # Removing omarchy-vm later falls back to Windows without rewriting.
        self.omarchy_installed = False
        self.assertEqual(machines.selected()["id"], "windows")
        self.assertEqual(json.loads(self.registry.read_text())["selected"], "omarchy")
        with self.assertRaises(ValueError): machines.select("unknown")

    def test_select_refused_during_maintenance(self):
        machines.initialize()
        with core.maintenance():
            with self.assertRaises(core.OperationError): machines.select("omarchy")
        self.assertEqual(machines.load()["selected"], "windows")


class OmarchyStatusTests(Base):
    def test_status_parsing_keeps_only_known_fields(self):
        parsed = omarchy.parse_status({**STATUS, "installMedia": "/home/example/private.iso", "guest": "running; rm", "extra": 1})
        self.assertEqual(parsed, {"running": True, "guest": None, "sshReady": True, "viewerOpen": False,
                                  "installMedia": True, "diskUsedBytes": 10457452544})
        stopped = omarchy.parse_status({"running": False, "guest": None, "sshReady": True, "diskUsedBytes": True})
        self.assertFalse(stopped["sshReady"]); self.assertIsNone(stopped["diskUsedBytes"])
        for value in (None, [], "running", {"running": "yes"}, {}):
            with self.assertRaises(ValueError): omarchy.parse_status(value)

    def test_classification(self):
        p = omarchy.parse_status
        self.assertEqual(omarchy.classify(p(STATUS), {})[0], "running")
        self.assertEqual(omarchy.classify(p({**STATUS, "sshReady": False}), {})[0], "starting")
        self.assertEqual(omarchy.classify(p({**STATUS, "guest": "paused"}), {})[0], "paused")
        self.assertEqual(omarchy.classify(p({**STATUS, "guest": "unreachable"}), {})[0], "error")
        self.assertEqual(omarchy.classify(p({"running": False}), {}), ("stopped", "Omarchy VM is stopped"))
        backup = {"action": "backup", "status": "running", "machineId": "omarchy"}
        self.assertEqual(omarchy.classify(p({"running": False}), backup)[1], "Backup in progress")
        # A Windows backup never changes the Omarchy VM's summary.
        self.assertEqual(omarchy.classify(p({"running": False}), {**backup, "machineId": "windows"})[1], "Omarchy VM is stopped")

    def test_probe_parsing(self):
        self.assertEqual(omarchy.parse_probe(PROBE), {"cpu": 6, "memoryBytes": 8113128*1024, "diskBytes": 109519568896,
                                                      "diskFreeBytes": 90137178112, "desktop": "available"})
        self.assertEqual(omarchy.parse_probe("password=x\ncpu=abc\ndesktop=unavailable")["cpu"], None)
        for value in ("", "cpu=4", "desktop=maybe"):
            with self.assertRaises(ValueError): omarchy.parse_probe(value)

    def test_cua_health_report_shape(self):
        client = MagicMock()
        client.__enter__.return_value.call.return_value = HEALTH
        with patch("core.Client", return_value=client):
            self.assertEqual(core.cua_check("cua", "failed")["status"], "ready")
            client.__enter__.return_value.call.return_value = {"structuredContent": {**HEALTH["structuredContent"], "overall": "degraded"}}
            self.assertEqual(core.cua_check("cua", "failed")["status"], "error")
            client.__enter__.return_value.call.return_value = {"structuredContent": []}
            self.assertEqual(core.cua_check("cua", "failed")["message"], "failed")

    def collect(self, deep=True, previous=None, **kwargs):
        machine = machines.omarchy_machine()
        client = MagicMock()
        client.__enter__.return_value.call.return_value = HEALTH
        with patch("omarchy.run", side_effect=fake_run(**kwargs)), patch("core.Client", return_value=client) as cua:
            state = omarchy.collect(core.settings(), machine, deep, previous)
        return state, cua

    def test_deep_check_maps_to_existing_check_structure(self):
        state, cua = self.collect()
        self.assertEqual(state["summary"], "Omarchy VM is ready")
        self.assertEqual({k: v["status"] for k, v in state["checks"].items()}, {"ssh": "ready", "desktop": "available", "cua": "ready"})
        self.assertEqual(state["resources"], {"cpu": 6, "memoryBytes": 8113128*1024, "diskBytes": 109519568896, "diskFreeBytes": 90137178112})
        self.assertEqual(state["allocation"], {"cpu": 6, "ram": "8G"})
        self.assertEqual(state["backups"][0]["label"], "Fresh install")
        self.assertNotIn("path", state["backups"][0]); self.assertNotIn("private detail", json.dumps(state))
        caps = state["capabilities"]
        self.assertTrue(caps["backup"] and caps["restore"] and caps["allocate"] and caps["lifecycle"])
        self.assertFalse(any(caps[k] for k in ("aside", "asideSupported", "console", "handoff", "admin", "rehearsal", "rollback", "catalogue")))
        cua.assert_called_once()

    def test_desktop_unavailable_skips_cua(self):
        state, cua = self.collect(probe=PROBE.replace("desktop=available", "desktop=unavailable"))
        self.assertEqual(state["checks"]["desktop"]["status"], "signed-out")
        self.assertEqual(state["checks"]["cua"]["status"], "offline")
        cua.assert_not_called()

    def test_ssh_failure_and_stopped_vm(self):
        state, _ = self.collect(probe=core.OperationError("private output"))
        self.assertEqual(state["checks"]["ssh"]["status"], "error"); self.assertEqual(state["resources"], {})
        self.assertNotIn("private output", json.dumps(state))
        stopped, cua = self.collect(status={"running": False, "guest": None, "sshReady": False})
        self.assertEqual(stopped["state"], "stopped")
        self.assertTrue(all(c["status"] == "offline" for c in stopped["checks"].values()))
        cua.assert_not_called()

    def test_shallow_poll_uses_status_only_and_keeps_previous_evidence(self):
        previous, _ = self.collect()
        calls = []
        def only_status(argv, timeout):
            calls.append(argv[1:])
            return json.dumps(STATUS)
        with patch("omarchy.run", side_effect=only_status):
            state = omarchy.collect(core.settings(), machines.omarchy_machine(), False, previous)
        self.assertEqual(calls, [["status", "--json"]])
        self.assertEqual(state["checks"], previous["checks"]); self.assertEqual(state["backups"], previous["backups"])
        self.assertEqual(state["summary"], "Omarchy VM is ready")

    def test_local_labels_override_manifest_and_ignore_bad_ids(self):
        core.write_json(core.state_dir()/"labels.json", {"omarchy": {"omarchy-20261026T205001Z": "Mine", "../x": "bad"}})
        rows = [*BACKUPS, {"id": "../escape", "verified": True}, {"id": "omarchy-20261027T000000Z", "label": "x\ny"}]
        with patch("omarchy.run", side_effect=fake_run(backups=rows)):
            result = omarchy.backups(machines.omarchy_machine())
        self.assertEqual({r["id"] for r in result}, {"omarchy-20261026T205001Z", "omarchy-20261027T000000Z"})
        labels = {r["id"]: r["label"] for r in result}
        self.assertEqual(labels["omarchy-20261026T205001Z"], "Mine")
        self.assertEqual(labels["omarchy-20261027T000000Z"], "Omarchy VM backup")


class SnapshotTests(Base):
    def setUp(self):
        super().setUp()
        for name, value in (("core.tcp", False), ("core.viewer_windows", []), ("core.backups", [])):
            self.patch(patch(name, return_value=value))

    def test_snapshot_has_selected_detail_and_both_summaries(self):
        with patch("omarchy.run", side_effect=fake_run()):
            state = core.collect(core.settings())
        self.assertEqual(state["machineId"], "windows")
        self.assertEqual([(m["id"], m["state"], m["active"], m["selected"]) for m in state["machines"]],
                         [("windows", "unreachable", False, True), ("omarchy", "running", True, False)])
        self.assertTrue(state["capabilities"]["asideSupported"])

    def test_unavailable_summary_does_not_break_selected_snapshot(self):
        with patch("omarchy.run", side_effect=fake_run(status=core.OperationError("down"))):
            state = core.collect(core.settings())
        self.assertEqual(state["machines"][1]["state"], "unknown")
        self.omarchy_installed = False
        self.assertEqual(len(core.collect(core.settings())["machines"]), 1)

    def test_previous_from_other_machine_or_placeholder_is_never_reused(self):
        machines.initialize(); machines.select("omarchy")
        foreign = {"machineId": "windows", "state": "running", "checks": {"ssh": core.check("ready", "Windows")}, "backups": [{"id": "x"}], "allocation": {"cpu": 2}}
        placeholder = {"machineId": "omarchy", "placeholder": True, "checks": {}, "backups": []}
        for previous in (foreign, placeholder):
            with patch("omarchy.run", side_effect=fake_run()):
                state = core.collect(core.settings(), previous=previous)
            self.assertEqual(state["machineId"], "omarchy")
            self.assertEqual(state["checks"], {})
            self.assertEqual(state["allocation"], {"cpu": 6, "ram": "8G"})
            self.assertEqual(state["limits"]["cpu"], min(16, os.cpu_count()))

    def test_windows_stop_survives_a_later_omarchy_operation(self):
        operations.record("stop", "complete", "Windows shut down")
        operations.record("start", "complete", "Omarchy VM started", machineId="omarchy")
        self.assertEqual(core.windows_reachability(core.operation()), ("stopped", "Windows is stopped"))
        operations.record("backup", "running", "Copying", machineId="omarchy")
        self.assertEqual(core.windows_reachability(core.operation())[1], "Windows is stopped")

    def test_history_is_filtered_per_machine(self):
        history.append({"requestId": "a", "action": "backup", "status": "complete", "machineId": "omarchy"})
        history.append({"requestId": "b", "action": "backup", "status": "complete"})
        with patch("omarchy.run", side_effect=fake_run()):
            self.assertEqual([h["requestId"] for h in core.collect(core.settings())["history"]], ["b"])
            self.assertEqual([h["requestId"] for h in core.collect(core.settings(), machine_id="omarchy")["history"]], ["a"])


class DaemonTests(Base):
    def setUp(self):
        super().setUp()
        loader = importlib.machinery.SourceFileLoader("omachinesctl", str(ROOT/"backend/omachinesctl"))
        spec = importlib.util.spec_from_loader("omachinesctl", loader)
        self.ctl = importlib.util.module_from_spec(spec); loader.exec_module(self.ctl)

    def test_select_emits_placeholder_and_discards_stale_results(self):
        machines.initialize()
        old = {"machineId": "windows", "operation": {"status": "running"}, "machines": [{"id": "windows", "selected": True}, {"id": "omarchy", "selected": False}]}
        placeholder = self.ctl.select("omarchy", old)
        self.assertEqual((placeholder["machineId"], placeholder["state"], placeholder["placeholder"]), ("omarchy", "checking", True))
        self.assertEqual([m["selected"] for m in placeholder["machines"]], [False, True])
        self.assertEqual(placeholder["operation"], old["operation"])
        config = {"demo": False}
        self.assertTrue(self.ctl.stale({"machineId": "windows"}, config))
        self.assertFalse(self.ctl.stale({"machineId": "omarchy"}, config))
        self.assertFalse(self.ctl.stale({"state": "unknown"}, config))
        for value in ("unknown", None, 3):
            with self.assertRaises(core.OperationError): self.ctl.select(value, old)


class OperationTests(Base):
    def setUp(self):
        super().setUp()
        self.enterContext(redirect_stdout(io.StringIO()))
        self.config = {"demo": False, "notify": False, "asideEnabled": True, "pollSeconds": 120}
        self.launch = self.patch(patch("operations.subprocess.Popen"))
        machines.initialize(); machines.select("omarchy")

    def argv(self):
        return self.launch.call_args.args[0]

    def test_background_and_terminal_routing(self):
        operations.request_action("start", self.config)
        self.assertNotIn("/usr/bin/omarchy-launch-terminal", self.argv())
        operations.record("start", "complete", "Done")
        operations.request_action("restore", self.config, True, {"backupId": "omarchy-20261026T205001Z"})
        self.assertEqual(self.argv()[0], "/usr/bin/omarchy-launch-terminal")
        self.assertEqual(core.operation()["machineId"], "omarchy")

    def test_windows_only_actions_and_ids_are_refused_for_omarchy(self):
        for action, params in [("console", {}), ("handoff", {}), ("aside", {}), ("inspect", {}), ("rollback", {}),
                               ("rehearse", {"backupId": "omarchy-20261026T205001Z"}), ("rehearsal-image", {}),
                               ("verify", {"backupId": "omachines-20260101T000000Z-abcdef"}),
                               ("restore", {"backupId": "omarchy-20261026T205001Z/../x"}),
                               ("allocate", {"cpu": 4, "ram": "8"})]:
            with self.subTest(action=action), self.assertRaises(core.OperationError):
                operations.request_action(action, self.config, True, params)
        self.launch.assert_not_called()
        machines.select("windows")
        with self.assertRaises(core.OperationError):
            operations.request_action("verify", self.config, True, {"backupId": "omarchy-20261026T205001Z"})
        self.launch.assert_not_called()

    def test_confirmation_for_another_machine_is_refused(self):
        with self.assertRaises(core.OperationError):
            operations.request_action("stop", self.config, True, {}, "windows")
        self.launch.assert_not_called()
        operations.request_action("stop", self.config, True, {}, "omarchy")
        self.launch.assert_called_once()

    def test_worker_stays_pinned_to_requested_machine(self):
        self.patch(patch("operations.settings", return_value=self.config))
        perform = self.patch(patch("operations.perform", return_value="Done"))
        token = operations.request_action("stop", self.config, True)
        machines.select("windows")  # the panel switched while the worker started
        self.assertEqual(operations.worker("stop", token), 0)
        self.assertEqual(perform.call_args.args[3]["id"], "omarchy")
        self.assertEqual(history.entries()[-1]["machineId"], "omarchy")
        # A machine removed before the worker starts fails closed.
        machines.select("omarchy")
        token = operations.request_action("start", self.config)
        self.omarchy_installed = False
        self.assertEqual(operations.worker("start", token), 1)
        self.assertEqual(perform.call_count, 1)

    def stream(self, lines, code=0):
        return Mock(stdout=iter(lines), returncode=code, wait=Mock(return_value=code))

    def test_backup_records_stages_and_confirms_result(self):
        machine = machines.omarchy_machine()
        operations.record("backup", "running", "Preparing", machineId="omarchy")
        lines = ["Shutting down the Omarchy VM...\n", "OMACHINES_STAGE:copying\n", "OMACHINES_STAGE:verifying\n",
                 "OMACHINES_STAGE:starting\n", "Omarchy VM is running\n", "Backup omarchy-20261027T101010Z saved and verified\n"]
        seen = []
        real = operations.record
        with patch("omarchy.subprocess.Popen", return_value=self.stream(lines)) as popen, \
                patch("operations.record", side_effect=lambda *a, **k: (seen.append(a[2]), real(*a, **k))[1]):
            message = omarchy.perform("backup", self.config, {}, machine)
        self.assertEqual(popen.call_args.args[0], [machine["connections"]["control"], "backup"])
        self.assertIn("omarchy-20261027T101010Z saved and verified", message)
        self.assertIn("starting again", message)
        self.assertEqual(seen[1:], ["Shutting down Omarchy VM for a consistent copy", "Copying the VM disk and firmware",
                                    "Verifying the archive and checksum", "Starting Omarchy VM again"])

    def test_failures_are_sanitized(self):
        machine = machines.omarchy_machine()
        for lines, expected in [(["omarchy-vm: not enough space for a backup with 10 GiB to spare; nothing was stopped\n"], "Not enough space"),
                                (["omarchy-vm: Install media is missing: /home/example/x.iso\n"], "Backup did not finish"),
                                (["private tar output\n"], "Backup did not finish")]:
            with self.subTest(lines=lines), patch("omarchy.subprocess.Popen", return_value=self.stream(lines, 1)):
                with self.assertRaisesRegex(core.OperationError, expected) as raised:
                    omarchy.perform("backup", self.config, {}, machine)
                self.assertNotIn("/home", str(raised.exception)); self.assertNotIn("private", str(raised.exception))
        with patch("omarchy.subprocess.Popen", return_value=self.stream(["done\n"])):
            with self.assertRaisesRegex(core.OperationError, "could not be confirmed"):
                omarchy.perform("backup", self.config, {}, machine)

    def test_restore_requires_typed_identity(self):
        machine = machines.omarchy_machine()
        name = "omarchy-20261026T205001Z"
        with patch("omarchy.run", side_effect=fake_run()), patch("omarchy.subprocess.Popen") as popen:
            for typed in ("", "RESTORE omarchy-20261026T205002Z", "restore " + name):
                with patch("builtins.input", return_value=typed), self.assertRaisesRegex(core.OperationError, "cancelled"):
                    omarchy.perform("restore", self.config, {"backupId": name}, machine)
            popen.assert_not_called()
            with patch("builtins.input", return_value="RESTORE omarchy-20261027T000000Z"), self.assertRaisesRegex(core.OperationError, "known backup"):
                omarchy.perform("restore", self.config, {"backupId": "omarchy-20261027T000000Z"}, machine)
            popen.return_value = self.stream(["Verifying the backup...\n", "Restored " + name + ".\n"])
            with patch("builtins.input", return_value="RESTORE " + name):
                omarchy.perform("restore", self.config, {"backupId": name}, machine)
        self.assertEqual(popen.call_args.args[0], [machine["connections"]["control"], "restore", name, "--replace-current"])

    def test_allocation_limits(self):
        machine = machines.omarchy_machine()
        with patch("omarchy.subprocess.Popen", return_value=self.stream(["Omarchy VM allocation: 4 CPUs, 8G RAM\n"])) as popen, \
                patch("omarchy.os.cpu_count", return_value=32), patch("omarchy.os.sysconf", side_effect=lambda k: 4096 if k == "SC_PAGE_SIZE" else 8*1024**2):
            for cpu, ram in ((17, 8), (0, 8), (4, 3), (4, 29), (4, 100)):
                with self.subTest(cpu=cpu, ram=ram), self.assertRaises(core.OperationError):
                    omarchy.perform("allocate", self.config, {"cpu": cpu, "ram": ram}, machine)
            popen.assert_not_called()
            self.assertIn("4 CPUs, 8 GiB", omarchy.perform("allocate", self.config, {"cpu": 4, "ram": 8}, machine))
        self.assertEqual(popen.call_args.args[0][1:], ["resources", "--cpus", "4", "--memory", "8G"])

    def test_label_is_local_and_needs_known_backup(self):
        machine = machines.omarchy_machine()
        with patch("omarchy.run", side_effect=fake_run()):
            with self.assertRaises(core.OperationError):
                omarchy.perform("label", self.config, {"backupId": "omarchy-20200101T000000Z", "label": "x"}, machine)
            omarchy.perform("label", self.config, {"backupId": "omarchy-20261026T205001Z", "label": "Before update"}, machine)
            self.assertEqual(omarchy.backups(machine)[0]["label"], "Before update")

    def test_open_focuses_existing_viewer_without_view(self):
        machine = machines.omarchy_machine()
        with patch("omarchy.hypr_windows", return_value=["0xabc"]), patch("omarchy.run") as run, patch("omarchy.subprocess.Popen") as popen:
            self.assertEqual(omarchy.perform("open", self.config, {}, machine), "Omarchy VM viewer focused")
        run.assert_called_once_with(["hyprctl", "dispatch", 'hl.dsp.focus({ window = "address:0xabc" })'], 3)
        popen.assert_not_called()

    def test_connection_guide_names_bridges_and_guest_sudo(self):
        text = omarchy.guide(machines.omarchy_machine())
        for value in ("omarchy-vm-ssh", "omarchy-vm-mcp", "passwordless sudo", "not on this host", "OmarchyVM"):
            self.assertIn(value, text)


if __name__ == "__main__":
    unittest.main()
