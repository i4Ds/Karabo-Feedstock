"""Regression tests for WSClean upgrade automation."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / ".github/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from dependency_scan import ScanError
from prepare_dependency_upgrade import prepare_wsclean_changes, verify_wsclean_tag, main

RECIPE = (ROOT / "wsclean/meta.yaml").read_text(encoding="utf-8")


class WSCleanPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "wsclean").mkdir()
        (self.root / "wsclean/meta.yaml").write_text(RECIPE, encoding="utf-8")
        self.release = self.root / "wsclean/release.json"
        self.release.write_text('{"version":"3.6.0","build_number":2}\n', encoding="utf-8")
        self.dependency = SimpleNamespace(
            id="wsclean", source_type="gitlab-tags", base_url="https://gitlab.com",
            project="aroffringa/wsclean", version_variables=("WSCLEAN_VERSION",),
            recipes=("wsclean/meta.yaml",), version_file="wsclean/release.json",
        )
        self.registry = SimpleNamespace(settings=SimpleNamespace(http_timeout_seconds=20))

    @patch("prepare_dependency_upgrade.verify_wsclean_tag")
    def test_updates_only_release_metadata_without_writing(self, verify):
        changes = prepare_wsclean_changes(self.root, self.registry, self.dependency,
                                          {"WSCLEAN_VERSION": "3.6.0"}, "3.7.0")
        self.assertEqual(list(changes), [self.release])
        self.assertEqual(json.loads(changes[self.release][1]),
                         {"version": "3.7.0", "build_number": 0})
        self.assertEqual(json.loads(self.release.read_text())["version"], "3.6.0")
        verify.assert_called_once_with("3.7.0", 20)

    @patch("prepare_dependency_upgrade.verify_wsclean_tag")
    def test_rejects_changed_recipe(self, verify):
        (self.root / "wsclean/meta.yaml").write_text(RECIPE.replace(
            'git_tag: "v{{ WSCLEAN_VERSION[:-2] }}"', 'git_tag: main'))
        with self.assertRaisesRegex(ScanError, "tag template"):
            prepare_wsclean_changes(self.root, self.registry, self.dependency,
                                    {"WSCLEAN_VERSION": "3.6.0"}, "3.7.0")
        verify.assert_not_called()

    @patch("prepare_dependency_upgrade.verify_wsclean_tag")
    def test_rejects_release_version_mismatch(self, verify):
        self.release.write_text('{"version":"3.5.0","build_number":0}\n')
        with self.assertRaisesRegex(ScanError, "mismatch"):
            prepare_wsclean_changes(self.root, self.registry, self.dependency,
                                    {"WSCLEAN_VERSION": "3.6.0"}, "3.7.0")
        verify.assert_not_called()

    @patch("prepare_dependency_upgrade.verify_wsclean_tag")
    def test_rejects_unexpected_registry(self, verify):
        self.dependency.project = "other/wsclean"
        with self.assertRaises(ScanError):
            prepare_wsclean_changes(self.root, self.registry, self.dependency,
                                    {"WSCLEAN_VERSION": "3.6.0"}, "3.7.0")
        verify.assert_not_called()

    @patch("prepare_dependency_upgrade.verify_wsclean_tag")
    def test_rejects_unexpected_repository(self, verify):
        path = self.root / "wsclean/meta.yaml"
        path.write_text(RECIPE.replace('https://gitlab.com/aroffringa/wsclean.git',
                                       'https://example.com/other.git'))
        with self.assertRaisesRegex(ScanError, "repository"):
            prepare_wsclean_changes(self.root, self.registry, self.dependency,
                                    {"WSCLEAN_VERSION": "3.6.0"}, "3.7.0")
        verify.assert_not_called()

    @patch("subprocess.run")
    def test_verifies_exact_upstream_tag(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "a" * 40 + "\trefs/tags/v3.7\n"
        verify_wsclean_tag("3.7.0")
        self.assertEqual(run.call_args.args[0][-1], "refs/tags/v3.7")

    @patch("subprocess.run")
    def test_rejects_missing_upstream_tag(self, run):
        run.return_value.returncode = 2
        run.return_value.stdout = ""
        with self.assertRaises(ScanError):
            verify_wsclean_tag("3.7.0")

    def test_rejects_invalid_tag_version(self):
        with self.assertRaises(ScanError):
            verify_wsclean_tag("3.7.1")


class WSCleanWorkflowTests(unittest.TestCase):
    def test_approval_is_safe(self):
        wf = yaml.safe_load((ROOT / ".github/workflows/validate-upgrade-approval.yml").read_text())
        job = wf["jobs"]["create-draft-pr"]
        self.assertIn('"wsclean"', job["if"])
        steps = {s.get("name"): s for s in job["steps"]}
        self.assertIn("expected='wsclean/release.json'", steps["Verify changed files"]["run"])
        self.assertIn("wsclean/release.json", steps["Create Draft PR"]["with"]["add-paths"])
        self.assertTrue(steps["Create Draft PR"]["with"]["draft"])

    def test_target_version_is_used_and_no_publish(self):
        wf = yaml.safe_load((ROOT / ".github/workflows/validate_dependency_pr.yml").read_text())
        job = wf["jobs"]["validate-wsclean"]
        self.assertIs(job["with"]["publish"], False)
        self.assertIn("wsclean_version", job["with"]["versionOverride"])
        self.assertIn("head", job["with"]["checkoutRef"])
        build = yaml.safe_load((ROOT / ".github/workflows/build_wsclean.yml").read_text())
        self.assertIn("resolve-version", build["jobs"])
        self.assertIn("versionOverride", build["jobs"]["WSClean"]["with"])


if __name__ == "__main__":
    unittest.main()
