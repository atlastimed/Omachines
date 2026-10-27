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
        self.state = core.state_dir()/"installer.json"

    def patch(self, p):
        result=p.start(); self.addCleanup(p.stop); return result

    def read(self): return json.loads(self.shell.read_text())
    def write(self, value): self.shell.write_text(json.dumps(value))
    def saved(self): return json.loads(self.state.read_text())

    def remove_entry(self):
        data = self.read()
        for section in data["bar"]["layout"].values():
            section[:] = [e for e in section if e["id"]!=install.ID]
        self.write(data)

    def entries(self):
        return [e for section in self.read()["bar"]["layout"].values() for e in section if e["id"]==install.ID]

    def command(self, *args):
        if args == ("omarchy-shell", "shell", "rescanPlugins"):
            self.hook()
        if args == ("omarchy", "plugin", "list", "--json"):
            return json.dumps([{"id":install.ID, "enabled":bool(self.entries())}] if self.destination.exists() else [])
        if len(args)>2 and args[2] in ("putBarWidget", "setBarWidget", "setPluginEnabled"):
            self.mutations.append(args)
            current = self.read()
            layout = current["bar"]["layout"]
            entries = [e for section in layout.values() for e in section if e["id"]==install.ID]
            if args[2] == "putBarWidget" and not entries:
                # Native: after the neighbour if it is in the section, else the clamped index.
                place = json.loads(args[4]); row = layout.setdefault(place["section"], [])
                ids = [e["id"] for e in row]
                row.insert(ids.index(place["after"])+1 if place.get("after") in ids else min(place["index"], len(row)), {"id":install.ID})
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
        # Options are read just before removal, including edits made meanwhile.
        self.assertEqual(self.saved(), {"options":{"notify":True}, "placement":{"section":"left","index":0}})

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
        with self.assertRaisesRegex(RuntimeError, "Run the same install.py command again"): self.invoke()
        self.assertTrue((self.destination/"manifest.json").exists())
        self.assert_edits_preserved()
        with core.maintenance(): pass
        self.run.side_effect=self.command; self.hook=lambda: None
        self.invoke()
        self.assertEqual(self.entries(), [{"id":install.ID, "notify":True}])
        self.assertFalse(self.state.exists())

    def test_every_option_survives_uninstall_and_reinstall(self):
        schema = json.loads((ROOT/"manifest.json").read_text())["barWidget"]["schema"]
        changed = {"boolean": lambda s: not s["defaultValue"], "integer": lambda s: s["max"],
                   "string": lambda s: "/trusted/provider"}
        options = {s["key"]: changed[s["type"]](s) for s in schema}  # new option types need a case here
        self.assertLessEqual({"pollSeconds","notify","asideEnabled","showLabel","setupWorkspace"}, set(options))
        self.assertFalse(options["notify"] or options["asideEnabled"])
        data = self.read(); data["bar"]["layout"]["right"][1] = {"id":install.ID, **options}; self.write(data)
        self.invoke("--uninstall")
        self.invoke("--uninstall")  # a repeated removal keeps the saved copy
        self.assertEqual(self.entries(), [])
        data = self.read(); data["unrelated"] = 2; data["bar"]["layout"]["right"][0]["value"] = 42; self.write(data)
        self.mutations.clear()
        self.invoke()
        self.assertEqual(self.read()["bar"]["layout"]["right"], [{"id":"other.plugin","value":42}, {"id":install.ID, **options}])
        self.assertEqual(self.read()["unrelated"], 2)
        self.assertEqual([x[2] for x in self.mutations], ["putBarWidget", *["setBarWidget"]*len(options)])
        self.assertFalse(self.state.exists())

    def test_reinstall_uses_saved_placement_with_native_fallback(self):
        self.write({"bar":{"layout":{"left":[{"id":"left.widget"}, {"id":install.ID}], "right":[]}}})
        self.invoke("--uninstall")
        data = self.read(); data["bar"]["layout"]["left"].insert(0, {"id":"new.widget"}); self.write(data)
        self.invoke()
        self.assertEqual([e["id"] for e in self.read()["bar"]["layout"]["left"]], ["new.widget", "left.widget", install.ID])
        self.invoke("--uninstall")
        data = self.read(); data["bar"]["layout"]["left"].remove({"id":"left.widget"}); self.write(data)
        self.invoke()
        self.assertEqual([e["id"] for e in self.read()["bar"]["layout"]["left"]], ["new.widget", install.ID])

    def test_retry_completes_initial_activation_that_failed_before_mutation(self):
        import shutil
        shutil.rmtree(self.destination); self.remove_entry()
        def fail_before_mutation(*args):
            if args[:3] == ("omarchy", "plugin", "validate") and args[3] != str(install.ROOT):
                # The activation intent is saved before the release is published.
                self.assertTrue(self.saved()["activationPending"]); self.assertFalse(self.destination.exists())
            if args[2:3] == ("putBarWidget",):
                raise OSError("fixture IPC failed before bar activation")
            return self.command(*args)
        self.run.side_effect = fail_before_mutation
        with self.assertRaisesRegex(RuntimeError, "Run the same install.py command again"): self.invoke()
        self.assertTrue((self.destination/"manifest.json").exists())
        self.assertEqual(self.entries(), [])
        self.assertTrue(self.saved()["activationPending"])
        self.run.side_effect = self.command
        self.invoke()
        self.invoke()  # a later update neither duplicates nor re-adds the entry
        self.assertEqual(self.read()["bar"]["layout"]["right"], [{"id":"other.plugin","value":1}, {"id":install.ID}])
        self.assertEqual([x[2] for x in self.mutations], ["putBarWidget"])
        self.assertFalse(self.state.exists())

    def test_retry_after_lost_acknowledgement_keeps_newer_options(self):
        data = self.read(); data["bar"]["layout"]["right"][1].update(pollSeconds=300, showLabel=False); self.write(data)
        self.invoke("--uninstall")
        def lose_ack(*args):
            result = self.command(*args)
            if args[2:3] == ("putBarWidget",): raise OSError("fixture lost IPC acknowledgement")
            return result
        self.run.side_effect = lose_ack
        with self.assertRaisesRegex(RuntimeError, "again"): self.invoke()
        self.assertEqual(self.entries(), [{"id":install.ID}])
        data = self.read(); data["bar"]["layout"]["right"][1]["pollSeconds"] = 600; self.write(data)  # changed in Omarchy
        self.run.side_effect = self.command
        self.invoke()
        self.assertEqual(self.entries(), [{"id":install.ID, "notify":False, "showLabel":False, "pollSeconds":600}])
        self.assertFalse(self.state.exists())

    def test_update_leaves_removed_widget_off_and_present_options_alone(self):
        core.write_json(self.state, {"options":{"notify":True, "pollSeconds":300}, "placement":{"section":"left","index":0}})
        self.invoke()
        self.assertEqual(self.entries(), [{"id":install.ID, "notify":False}])
        self.remove_entry()  # deliberately removed through Omarchy
        self.invoke()
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.mutations, [])

    def test_saved_state_is_validated_before_restore(self):
        import shutil
        junk = {"activationPending":"yes", "placement":{"section":"top","index":-1},
                "options":{"notify":"false", "pollSeconds":5, "asideEnabled":0, "showLabel":False,
                           "setupWorkspace":["/x"], "demo":True, "id":"other.plugin"}}
        for text, expected in (("not json", {}), ("[]", {}), (json.dumps(junk), {"showLabel":False})):
            with self.subTest(text=text):
                shutil.rmtree(self.destination, ignore_errors=True); self.remove_entry()
                self.state.write_text(text)
                self.invoke()
                self.assertEqual(self.read()["bar"]["layout"]["right"], [{"id":"other.plugin","value":1}, {"id":install.ID, **expected}])

    def test_explicit_workspace_overrides_saved_workspace_on_reinstall(self):
        work = self.home/"provider"; work.mkdir()
        (work/"create-development-backup.sh").touch()
        data = self.read(); data["bar"]["layout"]["right"][1]["setupWorkspace"] = "/old/provider"; self.write(data)
        self.invoke("--uninstall")
        self.invoke("--setup-workspace", str(work))
        self.assertEqual(self.entries(), [{"id":install.ID, "notify":False, "setupWorkspace":str(work)}])


if __name__ == "__main__": unittest.main()
