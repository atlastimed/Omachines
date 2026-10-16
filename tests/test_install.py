from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/"backend")]
import core
import install
import operations


class InstallerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.shell = self.home/".config/omarchy/shell.json"
        self.destination = self.shell.parent/"plugins"/install.ID
        self.destination.mkdir(parents=True)
        (self.destination/"manifest.json").write_text(json.dumps({"id":install.ID}))
        (self.destination/"old-runtime").touch()
        self.write({"bar":{"layout":{"right":[{"id":"other.plugin","value":1},
                    {"id":install.ID,"notify":False}], "left":[]}},"unrelated":1})
        self.patch(patch("pathlib.Path.home", return_value=self.home))
        self.patch(patch.dict(os.environ, {"XDG_STATE_HOME":str(self.home/"state")}))
        self.run = self.patch(patch("install.run", side_effect=self.command))
        self.patch(patch("install.subprocess.run"))  # rollback IPC only
        self.hook = lambda: None
        self.mutations = []

    def patch(self, p):
        result=p.start(); self.addCleanup(p.stop); return result

    def read(self): return json.loads(self.shell.read_text())
    def write(self, value): self.shell.write_text(json.dumps(value))

    def command(self, *args):
        if args == ("omarchy-shell", "shell", "rescanPlugins"):
            self.hook()
        if args == ("omarchy", "plugin", "list", "--json"):
            return json.dumps([{"id":install.ID}] if self.destination.exists() else [])
        if len(args)>2 and args[2] in ("putBarWidget", "setBarWidget", "setPluginEnabled"):
            self.mutations.append(args)
            current = self.read()
            layout = current["bar"]["layout"]
            entries = [e for section in layout.values() for e in section if e["id"]==install.ID]
            if args[2] == "putBarWidget" and not entries:
                layout["right"].insert(1, {"id":install.ID})
            if args[2] == "setBarWidget":
                entries[0][args[4]] = json.loads(args[5])
            if args[2] == "setPluginEnabled":
                for section in layout:
                    layout[section] = [e for e in layout[section] if e["id"]!=install.ID]
            self.write(current)
        return "ok"

    def invoke(self, *args):
        with redirect_stdout(io.StringIO()): install.main(list(args))

    def concurrent_edit(self):
        current = self.read()
        current["unrelated"] = 2
        current["bar"]["layout"]["right"][0]["value"] = 42
        # Even edits to Omachines itself must survive an update/rollback.
        entry = current["bar"]["layout"]["right"].pop()
        entry["notify"] = True
        current["bar"]["layout"]["left"].append(entry)
        self.write(current)

    def assert_edits_preserved(self):
        data = self.read()
        self.assertEqual(data["unrelated"], 2)
        self.assertEqual(data["bar"]["layout"]["right"][0]["value"], 42)
        self.assertTrue(data["bar"]["layout"]["left"][0]["notify"])

    def test_update_never_restores_old_settings(self):
        self.hook = self.concurrent_edit
        self.invoke()
        self.assert_edits_preserved()
        self.assertEqual(self.mutations, [])
        self.assertFalse((self.destination/"old-runtime").exists())

    def test_failed_update_rolls_back_files_only(self):
        def fail():
            self.concurrent_edit()
            raise RuntimeError("fixture")
        self.hook = fail
        with self.assertRaisesRegex(RuntimeError, "fixture"): self.invoke()
        self.assert_edits_preserved()
        self.assertTrue((self.destination/"old-runtime").exists())
        with core.maintenance(): pass  # failure did not retain exclusion

    def test_initial_install_preserves_concurrently_placed_widget(self):
        import shutil
        shutil.rmtree(self.destination)
        self.hook = self.concurrent_edit
        self.invoke()
        self.assert_edits_preserved()
        self.assertEqual([x[2] for x in self.mutations], ["putBarWidget"])

    def test_workspace_uses_native_single_field_mutation(self):
        work = self.home/"provider"; work.mkdir()
        (work/"create-development-backup.sh").touch()
        self.hook = self.concurrent_edit
        self.invoke("--setup-workspace", str(work))
        self.assert_edits_preserved()
        self.assertEqual(self.read()["bar"]["layout"]["left"][0]["setupWorkspace"], str(work))
        self.assertEqual([x[2] for x in self.mutations], ["setBarWidget"])

    def test_requests_and_second_installer_rejected_during_transaction(self):
        def during():
            with self.assertRaises(core.OperationError):
                operations.request_action("open", {"demo":False})
            with self.assertRaises(core.OperationError): self.invoke()
        self.hook = during
        self.invoke()
        self.assertFalse(core.busy())

    def test_uninstall_preserves_unrelated_settings(self):
        self.hook = self.concurrent_edit
        self.invoke("--uninstall")
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.read()["unrelated"], 2)
        self.assertEqual(self.read()["bar"]["layout"]["right"][0]["value"], 42)
        self.assertEqual(self.read()["bar"]["layout"]["left"], [])

    def test_invalid_config_rejected_before_changes(self):
        for value in ("not json", "[]", '{"bar":{"layout":[]}}'):
            self.shell.write_text(value)
            with self.assertRaises(ValueError): self.invoke()
            self.assertEqual(self.shell.read_text(), value)
            self.assertTrue((self.destination/"old-runtime").exists())
        self.run.assert_not_called()

    def test_symlink_config_rejected_without_replacing_target(self):
        target = self.shell.with_name("real-config")
        self.shell.rename(target); self.shell.symlink_to(target)
        original=target.read_bytes()
        with self.assertRaisesRegex(ValueError, "Symlinked"): self.invoke()
        self.assertTrue(self.shell.is_symlink())
        self.assertEqual(target.read_bytes(), original)
        self.run.assert_not_called()

    def test_settings_ack_failure_retains_successfully_installed_files_and_edits(self):
        import shutil
        shutil.rmtree(self.destination)
        def fail_ack(*args):
            result = self.command(*args)
            if len(args)>2 and args[2]=="putBarWidget":
                raise OSError("fixture lost IPC acknowledgement")
            return result
        self.run.side_effect=fail_ack
        self.hook=self.concurrent_edit
        with self.assertRaises(OSError): self.invoke()
        self.assertTrue((self.destination/"manifest.json").exists())
        self.assert_edits_preserved()
        with core.maintenance(): pass


if __name__ == "__main__": unittest.main()
