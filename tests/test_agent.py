import sealed  # refuses a direct run on the desktop
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ENTRY = Path(__file__).resolve().parents[1]/"backend/omachines-agent"
loader = importlib.machinery.SourceFileLoader("agent_cli", str(ENTRY))
agent = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(agent)


class AgentTests(unittest.TestCase):
    def test_desktop_probe_uses_live_compositor_including_older_ssh_environments(self):
        with tempfile.TemporaryDirectory() as folder:
            packages = Path(folder)
            manager = packages/"systemctl"
            manager.write_text("#!/bin/sh\necho HYPRLAND_INSTANCE_SIGNATURE=live\n")
            manager.chmod(0o755)
            compositor = packages/"hyprctl"
            compositor.write_text("#!/bin/sh\n"
                                  "[ \"$HYPRLAND_INSTANCE_SIGNATURE\" = live ] || exit 1\n"
                                  "echo '[{}]'\n")
            compositor.chmod(0o755)
            for signature, expected in (("", "available"), ("stale", "unavailable")):
                env = {**os.environ, "PATH": folder + os.pathsep + os.environ["PATH"],
                       "HYPRLAND_INSTANCE_SIGNATURE": signature}
                response = subprocess.run(["/bin/sh", "-c", agent.omarchy.PROBE], env=env,
                                          capture_output=True, text=True, check=True)
                self.assertEqual(agent.omarchy.parse_probe(response.stdout)["desktop"], expected)

    def test_cua_json_travels_on_stdin_for_both_shells(self):
        value = {"text": """$() `x` "' 雪""", "session": "qa"}
        for factory in (agent.machines.omarchy_machine, agent.machines.windows_machine):
            machine = factory()
            with self.subTest(machine=machine["id"]), patch.object(agent.subprocess, "run") as run:
                run.return_value.returncode = 7
                self.assertEqual(agent.cua(machine, "type_text", json.dumps(value), None), 7)
                argv = run.call_args.args[0]
                self.assertEqual(argv[0], machine["connections"]["ssh"])
                self.assertNotIn(value["text"], argv[1])
                self.assertEqual(json.loads(run.call_args.kwargs["input"]), value)

    def test_gui_readiness_needs_all_three_checks(self):
        partial = {"machine": "omarchy", "state": "running", "summary": "signing in", "maintenance": None,
                   "ssh": "ready", "desktop": "available", "cua": "error"}
        ready = {**partial, "cua": "ready"}
        with patch.object(agent, "snapshot", side_effect=[partial, ready]) as probe, patch.object(agent.time, "sleep"):
            self.assertEqual(agent.wait_ready(agent.machines.omarchy_machine(), {}, True), ready)
            self.assertEqual(probe.call_count, 2)

    def test_result_survives_a_later_operation(self):
        with patch.object(agent.core, "operation", return_value={"requestId": "next", "status": "complete"}), \
                patch.object(agent.history, "entries", return_value=[{"requestId": "mine", "status": "failed",
                                                                     "message": "Authorization cancelled"}]):
            with self.assertRaisesRegex(agent.core.OperationError, "Authorization cancelled"):
                agent.wait_operation("mine")

    def test_cannot_reach_real_guest_from_demo_mode(self):
        with patch.object(agent.core, "settings", return_value={"demo": True}), patch.object(agent.os, "execv") as run:
            with self.assertRaisesRegex(agent.core.OperationError, "demo mode"):
                agent.main(["omarchy", "run", "hostname"])
            run.assert_not_called()
