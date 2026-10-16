import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "privacy_check", Path(__file__).resolve().parents[1]/"scripts/privacy_check.py")
privacy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(privacy)


class PrivacyTests(unittest.TestCase):
    def test_identifying_paths_flagged_without_values(self):
        for prefix in ("/"+"home/", "/"+"Users/", "C:"+"\\Users\\"):
            separator = "\\" if prefix.startswith("C:") else "/"
            value = prefix+"sample-person"+separator+"project"
            self.assertEqual(privacy.findings(Path("fixture.txt"), value),
                             [(1, "personal home path")])

    def test_documentation_placeholders_allowed(self):
        values = "/home/example/project\nC:\\Users\\Example\\project\ndev@example.com"
        self.assertEqual(privacy.findings(Path("guide.md"), values), [])

    def test_email_report_contains_no_address(self):
        value = "developer"+"@"+"private.invalid"
        self.assertEqual(privacy.findings(Path("fixture.txt"), value),
                         [(1, "email address needs review")])

    def test_private_artifacts_rejected(self):
        for name in (".env", ".env.production", "id_ed25519", "vm.qcow2", "secret.pem"):
            self.assertEqual(privacy.findings(Path(name), ""),
                             [(0, "private artifact type")])
        self.assertEqual(privacy.findings(Path(".env.example"), ""), [])


if __name__ == "__main__":
    unittest.main()
