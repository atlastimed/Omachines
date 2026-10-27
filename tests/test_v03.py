"""Version 0.3: machine registry, Omarchy VM provider and Windows storage anchor.

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
from unittest.mock import MagicMock, Mock, mock_open, patch

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

    def test_v1_migration_keeps_windows_name_and_original_file(self):
        self.v1(name="My Windows", note="dropped")
        original = self.registry.read_text()
        data = machines.initialize()
        self.assertEqual(data["schemaVersion"], 2)
        self.assertEqual(data["selected"], "windows")
        self.assertEqual(data["machines"][0], {**machines.windows_machine(), "name": "My Windows"})
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

    def test_unknown_providers_and_shapes_fail_closed_and_preserve_file(self):
        cases = [
            {"schemaVersion": 2, "selected": "windows", "machines": [machines.windows_machine(), machines.windows_machine()]},
            {"schemaVersion": 2, "selected": "windows", "machines": [machines.windows_machine(), {**machines.omarchy_machine(), "provider": ["omarchy-qemu"]}]},
            {"schemaVersion": 2, "selected": "windows", "machines": [machines.windows_machine(), {**machines.omarchy_machine(), "id": "omarchy-2"}]},
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

    def stored(self, selected, windows, omarchy):
        value = {"schemaVersion": 2, "selected": selected, "machines": [windows, omarchy]}
        self.registry.write_text(json.dumps(value))
        return value

    def test_stored_fixed_fields_are_replaced_by_definitions_and_selection_kept(self):
        # v0.3.0 stored Omarchy with a host SSH port; its SSH now uses vsock.
        old_omarchy = {**machines.omarchy_machine(), "host": "127.0.0.1", "sshPort": 2223}
        self.stored("omarchy", machines.windows_machine(), old_omarchy)
        data = machines.load()
        self.assertEqual(data, {"schemaVersion": 2, "selected": "omarchy",
                                "machines": [machines.windows_machine(), machines.omarchy_machine()]})
        self.assertNotIn("sshPort", data["machines"][1]); self.assertNotIn("host", data["machines"][1])
        self.assertEqual(json.loads(self.registry.read_text()), data)
        self.assertEqual(machines.selected()["id"], "omarchy")

    def test_tampered_connections_are_never_used(self):
        windows = machines.windows_machine()
        tampered = {**windows, "consolePort": 9, "connections": {**windows["connections"], "ssh": "/tmp/evil-ssh"}}
        omarchy = {**machines.omarchy_machine(), "connections": {**machines.omarchy_machine()["connections"], "control": "/tmp/evil-vm"}}
        self.stored("windows", tampered, omarchy)
        self.assertEqual(machines.get("windows"), windows)
        self.assertEqual(machines.get("omarchy")["connections"], machines.omarchy_machine()["connections"])
        self.assertNotIn("evil", self.registry.read_text())
        self.assertEqual(json.loads(self.registry.read_text())["selected"], "windows")
        with patch("omarchy.run", side_effect=fake_run()) as run:
            core.collect(core.settings(), deep=True, machine_id="omarchy")
        self.assertTrue(run.call_args_list)
        self.assertTrue(all("evil" not in c.args[0][0] for c in run.call_args_list))

    def test_invalid_name_falls_back_and_migration_never_overwrites_newer_file(self):
        self.stored("windows", {**machines.windows_machine(), "name": "bad\nname"}, machines.omarchy_machine())
        self.assertEqual(machines.load()["machines"][0]["name"], "Windows 11")
        # A migration computed from an older read is dropped: a selection made
        # meanwhile is never overwritten.
        current = self.stored("omarchy", machines.windows_machine(), machines.omarchy_machine())
        machines.save({**current, "selected": "windows"}, expected={"schemaVersion": 2, "selected": "windows", "machines": []})
        self.assertEqual(json.loads(self.registry.read_text()), current)

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
                                  "installMedia": True, "diskUsedBytes": 10457452544, "unitState": None, "maintenance": None})
        stopped = omarchy.parse_status({"running": False, "guest": None, "sshReady": True, "diskUsedBytes": True})
        self.assertFalse(stopped["sshReady"]); self.assertIsNone(stopped["diskUsedBytes"])
        for value in (None, [], "running", {"running": "yes"}, {}):
            with self.assertRaises(ValueError): omarchy.parse_status(value)

    def test_unit_state_and_maintenance_are_validated(self):
        parsed = omarchy.parse_status({**STATUS, "unitState": "deactivating", "maintenance": "backup"})
        self.assertEqual((parsed["unitState"], parsed["maintenance"]), ("deactivating", "backup"))
        for name in ("backup", "restore", "resources", "create", "stop"):
            self.assertEqual(omarchy.parse_status({**STATUS, "maintenance": name})["maintenance"], name)
        for unit, maintenance in (("rebooting", "upgrade"), ("", ""), (["failed"], ["backup"]), (1, {"backup": 1}), (None, None)):
            with self.subTest(unit=unit, maintenance=maintenance):
                parsed = omarchy.parse_status({**STATUS, "unitState": unit, "maintenance": maintenance})
                self.assertEqual((parsed["unitState"], parsed["maintenance"]), (None, None))

    def test_unit_state_classification(self):
        p = omarchy.parse_status
        self.assertEqual(omarchy.classify(p({**STATUS, "unitState": "deactivating"}), {}), ("stopping", "Omarchy VM is shutting down"))
        self.assertEqual(omarchy.classify(p({"running": False, "unitState": "failed"}), {}),
                         ("stopped", "Omarchy VM stopped unexpectedly; check journalctl --user -u omarchy-vm"))
        self.assertEqual(omarchy.classify(p({"running": False, "unitState": "inactive"}), {}), ("stopped", "Omarchy VM is stopped"))
        self.assertEqual(omarchy.classify(p({"running": True, "guest": "unreachable", "unitState": "activating"}), {}),
                         ("starting", "Omarchy VM is starting"))
        self.assertEqual(omarchy.classify(p({**STATUS, "unitState": "active"}), {})[0], "running")
        # Model.js knows each state the provider reports.
        model = (ROOT/"Model.js").read_text()
        for state in ("running", "starting", "paused", "stopping", "error"):
            self.assertIn(state + ':"', model)

    def test_outside_maintenance_is_named_unless_ours(self):
        p = omarchy.parse_status
        st = p({"running": False, "unitState": "inactive", "maintenance": "backup"})
        self.assertEqual(omarchy.classify(st, {}), ("stopped", "Backup in progress outside the panel"))
        self.assertEqual(omarchy.classify(p({**STATUS, "maintenance": "resources"}), {})[1], "Resource change in progress outside the panel")
        ours = {"action": "backup", "status": "running", "machineId": "omarchy"}
        self.assertEqual(omarchy.classify(st, ours)[1], "Backup in progress")
        self.assertEqual(omarchy.classify(st, {**ours, "machineId": "windows"})[1], "Backup in progress outside the panel")
        self.assertEqual(omarchy.classify(st, {**ours, "status": "complete"})[1], "Backup in progress outside the panel")

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

    def test_status_failure_is_reported_as_this_machines_state(self):
        previous, _ = self.collect()
        for failure in (core.OperationError("private output"), ValueError("bad json"), OSError("gone")):
            with self.subTest(failure=failure):
                state, cua = self.collect(previous=previous, status=failure)
                self.assertEqual((state["state"], state["summary"]), ("unknown", "Omarchy VM status is unavailable"))
                self.assertEqual({k: v["status"] for k, v in state["checks"].items()}, {"ssh": "unknown", "desktop": "unknown", "cua": "unknown"})
                self.assertEqual(state["backups"], previous["backups"]); self.assertTrue(state["capabilities"]["lifecycle"])
                self.assertNotIn("private output", json.dumps(state)); cua.assert_not_called()

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

    def test_outside_maintenance_disables_checks_without_probes(self):
        for status in ({**STATUS, "maintenance": "stop", "unitState": "active"},
                       {"running": False, "unitState": "inactive", "maintenance": "restore"}):
            with self.subTest(status=status):
                state, cua = self.collect(status=status, probe=AssertionError("no guest probe"))
                self.assertEqual(state["maintenance"], status["maintenance"])
                self.assertTrue(state["summary"].endswith("in progress outside the panel"))
                self.assertEqual({c["message"] for c in state["checks"].values()}, {"Unavailable during this operation"})
                cua.assert_not_called()
        operations.record("restore", "running", "Restoring", machineId="omarchy")
        with patch("core.operation_locked", return_value=True):
            ours, _ = self.collect(status={"running": False, "maintenance": "restore"})
        self.assertIsNone(ours["maintenance"]); self.assertEqual(ours["summary"], "Restore in progress")

    def test_checks_are_due_again_when_outside_maintenance_ends(self):
        during, _ = self.collect(status={**STATUS, "maintenance": "create"}, probe=AssertionError("no guest probe"))
        after, _ = self.collect(deep=False, previous={**during, "machineId": "omarchy"})
        self.assertIsNone(after["maintenance"])
        self.assertEqual({c["status"] for c in after["checks"].values()}, {"unknown"})
        self.assertIn("checks are due", after["summary"])

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

    def test_selected_omarchy_status_failure_keeps_identity_and_switcher(self):
        machines.initialize(); machines.select("omarchy")
        with patch("omarchy.run", side_effect=fake_run(status=core.OperationError("timed out"))):
            state = core.collect(core.settings())
        self.assertEqual((state["machineId"], state["state"]), ("omarchy", "unknown"))
        self.assertEqual([(m["id"], m["selected"]) for m in state["machines"]], [("windows", False), ("omarchy", True)])

    def test_failed_collection_is_attributed_to_the_selected_machine(self):
        machines.initialize(); machines.select("omarchy")
        windows = {"machineId": "windows", "name": "Windows 11", "state": "running", "capabilities": {"aside": True},
                   "backups": [{"id": "x"}], "history": [{"requestId": "w"}],
                   "machines": [{"id": "windows", "selected": True}, {"id": "omarchy", "selected": False}]}
        with patch("core.collect", side_effect=RuntimeError("bug")):
            failed = core.refresh_snapshot(core.settings(), windows, True)
            same = core.refresh_snapshot(core.settings(), {**windows, "machineId": "omarchy"}, True)
        self.assertEqual((failed["machineId"], failed["name"], failed["state"]), ("omarchy", "Omarchy VM", "unknown"))
        self.assertEqual((failed["capabilities"], failed["backups"], failed["history"]), ({}, [], []))
        self.assertEqual([m["selected"] for m in failed["machines"]], [False, True])
        self.assertEqual(same["backups"], [{"id": "x"}])  # same machine: unchanged behaviour

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

    def test_lost_windows_ssh_invalidates_readiness_until_checked_again(self):
        ready = {k: core.check(s, "old") for k, s in (("ssh", "ready"), ("desktop", "available"), ("cua", "ready"), ("aside", "ready"))}
        def poll(ports, previous):
            with patch("core.tcp", side_effect=lambda p: p in ports), patch("core.viewer_windows", return_value=[]), \
                 patch("core.backups", return_value=[]), patch("core.capabilities", return_value={"ssh": True, "cua": True, "aside": True}):
                return {**core.collect_windows({"demo": False, "pollSeconds": 120}, previous=previous), "machineId": "windows"}
        before = {"state": "running", "summary": "Windows is ready", "checks": ready, "machineId": "windows"}
        lost = poll({8006}, before)
        self.assertEqual(lost["state"], "starting")
        self.assertEqual({k: v["status"] for k, v in lost["checks"].items()},
                         {"ssh": "offline", "desktop": "unknown", "cua": "unknown", "aside": "unknown"})
        back = poll({2222, 8006}, lost)
        self.assertEqual(back["state"], "running")
        self.assertIn("checks are due", back["summary"])
        self.assertEqual({v["status"] for v in back["checks"].values()}, {"unknown"})
        # The service checks the connections at once rather than at the next deep poll.
        self.assertTrue(self.ctl.regained(lost, back))
        self.assertFalse(self.ctl.regained(before, back))
        self.assertFalse(self.ctl.regained({**lost, "state": "unknown"}, back))
        self.assertFalse(self.ctl.regained({**lost, "machineId": "omarchy"}, back))

    def test_outside_maintenance_ending_counts_as_regained(self):
        during = {"state": "running", "maintenance": "resources", "machineId": "omarchy"}
        after = {"state": "running", "maintenance": None, "machineId": "omarchy"}
        self.assertTrue(self.ctl.regained(during, after))
        self.assertFalse(self.ctl.regained(after, after))
        self.assertFalse(self.ctl.regained({**during, "state": "stopped"}, during))

    def test_follow_up_checks_while_the_guest_signs_in(self):
        def snapshot(desktop="signed-out", cua="offline", **extra):
            return {"state": "running", "operation": {}, "checks": {"desktop": core.check(desktop, ""), "cua": core.check(cua, "")}, **extra}
        due = self.ctl.follow_up
        # SSH came back at 100 and a deep check ran then: every 20 s for 5 minutes.
        self.assertIs(due(snapshot(), 100, 100, 110), False)
        self.assertIs(due(snapshot(), 100, 100, 120), True)
        self.assertIs(due(snapshot("available", "error"), 100, 380, 400), True)
        self.assertIs(due(snapshot(), 100, 380, 401), None)
        # Settled: both ready, or Cua not configured.
        self.assertIs(due(snapshot("available", "ready"), 100, 100, 140), None)
        self.assertIs(due(snapshot("available", "missing"), 100, 100, 140), None)
        # Never during an operation; nothing to follow without a return of SSH.
        for status in ("starting", "running"):
            self.assertIs(due(snapshot(operation={"status": status}), 100, 100, 140), False)
        self.assertIs(due(snapshot(operation={"status": "complete"}), 100, 100, 140), True)
        self.assertIs(due(snapshot(), None, 0, 140), None)
        self.assertIs(due(snapshot(state="stopped"), 100, 100, 140), None)
        self.assertIs(due(snapshot(maintenance="backup"), 100, 100, 140), None)
        self.assertIs(due({"state": "running", "operation": {}, "checks": {}}, 100, 100, 140), True)


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
        return Mock(stdout=io.StringIO("".join(lines)), returncode=code, wait=Mock(return_value=code))

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

    def perform(self, action, lines, code=0, parameters=None):
        seen, real = [], operations.record
        with patch("omarchy.subprocess.Popen", return_value=self.stream(lines, code)) as popen, patch("omarchy.run", side_effect=fake_run()), \
                patch("operations.record", side_effect=lambda *a, **k: (seen.append(a[2]), real(*a, **k))[1]), \
                patch("builtins.input", return_value="RESTORE omarchy-20261026T205001Z"):
            try:
                return omarchy.perform(action, self.config, parameters or {}, machines.omarchy_machine()), seen, popen
            except core.OperationError as error:
                return error, seen, popen

    def test_provider_runs_detached_from_the_operation_terminal(self):
        for action, parameters in (("start", {}), ("stop", {}), ("backup", {}), ("restore", {"backupId": "omarchy-20261026T205001Z"})):
            with self.subTest(action=action):
                _, _, popen = self.perform(action, ["Backup omarchy-20261027T101010Z saved and verified\n"], parameters=parameters)
                self.assertIs(popen.call_args.kwargs["start_new_session"], True)
                self.assertEqual(popen.call_args.kwargs["stdin"], omarchy.subprocess.DEVNULL)

    def test_forced_stop_is_reported(self):
        clean, _, _ = self.perform("stop", ["Omarchy VM is stopped\n"])
        self.assertEqual(clean, "Omarchy VM shut down")
        forced, _, _ = self.perform("stop", ["Shutting down the Omarchy VM...\n", "Forced off: the guest did not shut down cleanly\n",
                                             "Omarchy VM is stopped\n"])
        self.assertEqual(forced, "Omarchy VM was forced off; it did not shut down cleanly")

    def test_forced_backup_and_backup_that_did_not_restart(self):
        saved = "Backup omarchy-20261027T101010Z saved and verified"
        forced, _, _ = self.perform("backup", ["Forced off: the guest did not shut down cleanly\n", "OMACHINES_STAGE:copying\n",
                                               "OMACHINES_STAGE:starting\n", saved + "\n"])
        self.assertEqual(forced, saved + " (the VM had to be forced off first); Omarchy VM is starting again")
        failed, _, _ = self.perform("backup", [saved + "\n", "omarchy-vm: backup saved, but the VM did not start again: only 3 GiB of "
                                               "memory is free; Omarchy VM needs 8 GiB. Shut down the other VM or close apps first\n"], 1)
        self.assertIsInstance(failed, core.OperationError)
        self.assertEqual(str(failed), saved + ", but Omarchy VM did not start again: only 3 GiB of memory is free; "
                                              "Omarchy VM needs 8 GiB. Shut down the other VM or close apps first")
        hidden, _, _ = self.perform("backup", [saved + "\n", "omarchy-vm: backup saved, but the VM did not start again: /dev/kvm busy\n"], 1)
        self.assertEqual(str(hidden), saved + ", but Omarchy VM did not start again: no reason was reported")
        # Without the saved line, nothing claims a backup.
        refused, _, _ = self.perform("backup", ["omarchy-vm: maintenance in progress (restore); try again when it finishes\n"], 1)
        self.assertEqual(str(refused), "Maintenance in progress (restore); try again when it finishes")

    def test_restore_that_did_not_restart_names_the_restored_backup(self):
        name = "omarchy-20261026T205001Z"
        lines = ["Verifying the backup...\n", "OMACHINES_STAGE:restored\n", "omarchy-vm: restored, but the VM did not start again: QEMU exited\n"]
        failed, seen, _ = self.perform("restore", lines, 1, {"backupId": name})
        self.assertIn("Backup restored", seen)
        self.assertEqual(str(failed), "Omarchy VM was restored from " + name + " but did not start: QEMU exited. The previous disk was kept aside")
        # Failing before the disks were swapped keeps the current disk.
        early, seen, _ = self.perform("restore", ["omarchy-vm: backup checksum does not match\n"], 1, {"backupId": name})
        self.assertEqual(str(early), "Backup checksum does not match"); self.assertNotIn("Backup restored", seen)
        done, _, _ = self.perform("restore", ["OMACHINES_STAGE:restored\n"], 0, {"backupId": name})
        self.assertEqual(done, "Omarchy VM restored from " + name + "; the previous disk was kept aside")

    def test_lock_and_memory_refusals_are_shown(self):
        for line, expected in (("omarchy-vm: maintenance in progress (backup); try again when it finishes",
                                "Maintenance in progress (backup); try again when it finishes"),
                               ("omarchy-vm: only 3 GiB of memory is free; Omarchy VM needs 8 GiB. Shut down the other VM or close apps first",
                                "Only 3 GiB of memory is free; Omarchy VM needs 8 GiB. Shut down the other VM or close apps first")):
            for action in ("start", "open"):
                with self.subTest(action=action, line=line), patch("omarchy.hypr_windows", return_value=[]):
                    self.assertEqual(str(self.perform(action, [line + "\n"], 1)[0]), expected)

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


class WindowsMemoryTests(Base):
    """Windows starts only when the host has its configured RAM free."""

    def setUp(self):
        super().setUp()
        self.meminfo = self.path/"meminfo"
        self.patch(patch("machines.MEMINFO", self.meminfo))
        self.allocation = {"allocation": {"cpu": 4, "ram": "8G"}}
        self.patch(patch("machines.admin_state", side_effect=lambda: self.allocation))
        self.reachable = self.patch(patch("operations.windows_reachability", return_value=("unreachable", "Windows is not reachable")))
        self.priv = self.patch(patch("operations.vm_priv"))
        self.launch = self.patch(patch("operations.subprocess.Popen"))
        self.patch(patch("operations.viewer_windows", return_value=[]))
        self.patch(patch("operations.wrapper", side_effect=lambda name: "/fixture/" + name))

    def free(self, gib):
        self.meminfo.write_text("MemTotal:       32000000 kB\nMemFree:  100 kB\nMemAvailable:   %d kB\n" % int(gib*1024**2))

    def perform(self, action):
        return operations.perform(action, {"demo": False}, {}, machines.windows_machine())

    def test_start_refused_below_configured_ram(self):
        self.free(7.95)
        with self.assertRaises(core.OperationError) as refused: self.perform("start")
        self.assertEqual(str(refused.exception), "Only 7.9 GiB of memory is free; Windows needs 8 GiB. Shut down the other VM or close apps first.")
        self.priv.assert_not_called()
        self.free(8)
        self.perform("start"); self.priv.assert_called_once_with("up")

    def test_configured_ram_or_eight_gib(self):
        self.allocation = {"allocation": {"cpu": 4, "ram": "16G"}}; self.free(12)
        with self.assertRaisesRegex(core.OperationError, "^Only 12 GiB of memory is free; Windows needs 16 GiB"): self.perform("start")
        for unknown in ({}, {"allocation": "8G"}, {"allocation": {"ram": "unknown"}}):
            with self.subTest(state=unknown):
                self.allocation = unknown; self.free(6)
                with self.assertRaisesRegex(core.OperationError, "Windows needs 8 GiB"): self.perform("start")
        self.priv.assert_not_called()

    def test_open_checks_memory_only_when_windows_is_stopped(self):
        self.free(2)
        with self.assertRaisesRegex(core.OperationError, "^Only 2 GiB"): self.perform("open")
        self.launch.assert_not_called()
        self.reachable.return_value = ("running", "Windows is reachable")
        self.assertEqual(self.perform("open"), "Windows viewer requested")
        self.assertEqual(self.launch.call_args.args[0], ["/fixture/windows-vm-launch"])


class WindowsAnchorTests(Base):
    """After a host restart the protected storage bind is missing until __priv up."""

    def setUp(self):
        super().setUp()
        self.enterContext(redirect_stdout(io.StringIO()))
        self.anchor = self.path/"mounts/uid-1000/storage"; self.anchor.mkdir(parents=True)
        self.patch(patch("operations.windows_anchor", return_value=self.anchor))
        self.mounted = [False]
        # Mounts come from the kernel table; the storage is the anchor itself,
        # so identity holds unless a test points it elsewhere.
        self.patch(patch("operations.mount_points", side_effect=lambda: {str(self.anchor)} if self.mounted[0] else set()))
        self.storage = self.patch(patch("operations.windows_storage", return_value=self.anchor))
        self.patch(patch("operations.os.path.ismount", side_effect=AssertionError("ismount misses same-filesystem binds")))
        self.events = []
        def priv(action):
            self.events.append(action)
            if action == "up": self.mounted[0] = True
        self.priv = self.patch(patch("operations.vm_priv", side_effect=priv))
        self.ready = self.patch(patch("operations.wait_windows_ready", return_value=True))
        self.admin = self.patch(patch("operations.admin_action", side_effect=lambda *a: self.events.append("admin") or "Complete backup saved and verified"))
        self.patch(patch("operations.windows_reachability", return_value=("unreachable", "Windows is not reachable")))
        self.memory = self.patch(patch("operations.ensure_memory"))
        helper = Mock(); helper.is_file.return_value = True
        real = Path
        self.patch(patch("operations.Path", side_effect=lambda *a: helper if a == ("/usr/local/lib/omachines/admin.py",) else real(*a)))
        self.windows = machines.windows_machine()

    def backup(self):
        return operations.perform("backup", {"demo": False}, {}, self.windows)

    def test_missing_anchor_starts_waits_stops_then_backs_up(self):
        message = self.backup()
        self.assertEqual(self.events, ["up", "down", "admin"])
        self.ready.assert_called_once()
        self.assertIn("left stopped", message)

    def test_mounted_anchor_uses_normal_backup(self):
        self.mounted[0] = True
        self.assertEqual(self.backup(), "Complete backup saved and verified")
        self.assertEqual(self.events, ["admin"])

    def test_running_windows_or_unsupported_layout_is_not_restarted(self):
        with patch("operations.windows_reachability", return_value=("running", "Windows is reachable")):
            self.backup()
        self.anchor.rmdir(); self.anchor.parent.rmdir()
        self.backup()
        self.assertEqual(self.events, ["admin", "admin"])

    def test_not_ready_is_shut_down_and_not_backed_up(self):
        self.ready.return_value = False
        with self.assertRaisesRegex(core.OperationError, "did not finish starting"): self.backup()
        self.assertEqual(self.events, ["up", "down"])

    def test_still_unmounted_after_start_is_shut_down(self):
        self.priv.side_effect = lambda action: self.events.append(action)
        with self.assertRaisesRegex(core.OperationError, "still not attached"): self.backup()
        self.assertEqual(self.events, ["up", "down"])
        self.ready.assert_not_called()

    def test_attached_storage_with_wrong_identity_is_shut_down(self):
        other = self.path/"other"; other.mkdir()
        self.storage.return_value = other
        with self.assertRaisesRegex(core.OperationError, "still not attached"): self.backup()
        self.assertEqual(self.events, ["up", "down"])
        self.ready.assert_not_called()

    def test_short_memory_refuses_before_starting_windows(self):
        self.memory.side_effect = core.OperationError("Only 2 GiB of memory is free")
        with self.assertRaisesRegex(core.OperationError, "Only 2 GiB"): self.backup()
        self.assertEqual(self.events, [])

    def test_cancelled_authorization_changes_nothing(self):
        self.priv.side_effect = core.OperationError("VM operation failed or authentication was cancelled")
        with self.assertRaisesRegex(core.OperationError, "cancelled"): self.backup()
        self.priv.assert_called_once_with("up")
        self.admin.assert_not_called()


class MountTableTests(unittest.TestCase):
    TABLE = ("36 25 0:32 / / rw - btrfs /dev/root rw\n"
             "90 36 0:32 /home/example/.windows /var/lib/omarchy/windows/mounts/anchor/storage rw - btrfs /dev/root rw\n"
             "91 36 0:32 /x /mnt/with\\040space rw - btrfs /dev/root rw\n")

    def test_same_filesystem_bind_is_found_and_escapes_decoded(self):
        with patch("operations.open", mock_open(read_data=self.TABLE), create=True):
            points = operations.mount_points()
        self.assertIn("/var/lib/omarchy/windows/mounts/anchor/storage", points)
        self.assertIn("/mnt/with space", points)
        with patch("operations.open", side_effect=OSError, create=True):
            self.assertEqual(operations.mount_points(), set())

    def test_attached_requires_the_mount_and_the_storage_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage, other = Path(tmp)/"storage", Path(tmp)/"other"
            storage.mkdir(); other.mkdir()
            with patch("operations.windows_storage", return_value=storage):
                with patch("operations.mount_points", return_value=set()):
                    self.assertFalse(operations.windows_storage_attached(storage))
                with patch("operations.mount_points", return_value={str(storage), str(other)}):
                    self.assertTrue(operations.windows_storage_attached(storage))
                    self.assertFalse(operations.windows_storage_attached(other))
                    self.assertFalse(operations.windows_storage_attached(Path(tmp)/"missing"))


class ReadinessTests(unittest.TestCase):
    def test_wait_returns_after_ssh_answers(self):
        with patch("operations.wrapper", return_value="ssh"), patch("core.guest_status", side_effect=[core.OperationError("x"), {"desktop": "available"}]), \
                patch("operations.time.sleep") as sleep:
            self.assertTrue(operations.wait_windows_ready())
        sleep.assert_called_once_with(5)

    def test_wait_is_bounded(self):
        clock = iter(range(0, 10000, 100))
        with patch("operations.wrapper", return_value="ssh"), patch("core.guest_status", side_effect=core.OperationError("x")), \
                patch("operations.time.sleep"), patch("operations.time.monotonic", side_effect=lambda: next(clock)):
            self.assertFalse(operations.wait_windows_ready(300))


if __name__ == "__main__":
    unittest.main()
