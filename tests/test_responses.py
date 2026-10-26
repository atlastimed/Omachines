import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"backend"))
import core


class ResponseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.patch(patch.dict(os.environ, {"XDG_STATE_HOME":tmp.name}))
        self.patch(patch("machines.available", lambda m: m["provider"] == "omarchy-windows"))
        self.patch(patch("core.viewer_windows", return_value=[]))
        self.patch(patch("core.backups", return_value=[]))
        self.patch(patch("core.tcp", return_value=True))
        self.patch(patch("core.capabilities", return_value={"ssh":True,"cua":True,"aside":False}))
        self.guest = self.patch(patch("core.guest_status", return_value={"desktop":"available","cpu":4}))
        self.patch(patch('core.aside_cli_status',return_value={'installed':True,'version':'1.2.3','authentication':'unknown'}))
        self.patch(patch('core.run',side_effect=AssertionError('Unmocked external command in response fixture')))
        self.client = self.patch(patch("core.Client")).return_value.__enter__.return_value
        self.client.call.return_value = {"structuredContent":{"schema_version":"1","overall":"ok"}}
        self.config = core.settings()

    def patch(self, p):
        result = p.start(); self.addCleanup(p.stop); return result

    def test_optional_guest_fields_are_type_checked(self):
        for value in (None, 1, [], {}):
            with self.subTest(value=value):
                result = core.parse_guest({"asidePath":value,"boot":value})
                self.assertEqual(result["asidePath"], "")
                self.assertEqual(result["boot"], "")

    def test_invalid_guest_envelopes_invalidate_old_readiness(self):
        old = core.collect(self.config, deep=True)
        for value in (None, 1, [], "string"):
            self.guest.return_value = value
            result = core.refresh_snapshot(self.config, old, True)
            self.assertEqual(result["checks"]["ssh"]["status"], "error")
            self.assertEqual(result["resources"], {})
            shallow = core.refresh_snapshot(self.config, result, False)
            self.assertEqual(shallow["checks"]["ssh"]["status"], "error")
            self.assertNotEqual(shallow["summary"], "Windows is ready")

    def test_invalid_cua_preserves_independent_ssh_evidence(self):
        for value in (None, 1, [], {"structuredContent":None}, {"structuredContent":[]}):
            self.client.call.return_value = value
            result = core.collect(self.config, deep=True)
            self.assertEqual(result["checks"]["ssh"]["status"], "ready")
            self.assertEqual(result["checks"]["desktop"]["status"], "available")
            self.assertEqual(result["checks"]["cua"]["status"], "error")
            self.assertEqual(result["resources"]["cpu"], 4)

    def test_unexpected_failure_survives_shallow_polls_then_recovers(self):
        old = core.collect(self.config, deep=True)
        with patch("core.collect", side_effect=RuntimeError("private")):
            failed = core.refresh_snapshot(self.config, old, True)
        self.assertTrue(failed["error"])
        self.assertNotIn("private", failed["error"])
        shallow = core.refresh_snapshot(self.config, failed, False)
        self.assertEqual(shallow["error"], failed["error"])
        self.assertTrue(all(c["status"]=="error" for c in shallow["checks"].values()))
        recovered = core.refresh_snapshot(self.config, shallow, True)
        self.assertEqual(recovered["error"], "")
        self.assertEqual(recovered["checks"]["cua"]["status"], "ready")


if __name__ == "__main__": unittest.main()
