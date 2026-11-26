import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_dependency_upgrade as planner

ROOT = Path(__file__).resolve().parents[3]

class TestEidosUpgrade(unittest.TestCase):
    def test_tag_resolution(self):
        ref = "refs/tags/v1.2.0"
        for output, expected in (
            (f"{'a'*40} {ref}\n", "a"*40),
            (f"{'a'*40} {ref}\n{'b'*40} {ref}^{{}}\n", "b"*40),
            (f"{'a'*40} refs/tags/1.2.0\n", "a"*40),
            (f"{'a'*40} {ref}\n{'b'*40} refs/tags/1.2.0\n", None),
            ("", None), (f"bad {ref}\n", None),
            (f"{'a'*40} {ref}^{{}}\n", None),
        ):
            with self.subTest(output=output), patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                if expected is None:
                    with self.assertRaises(planner.ScanError):
                        planner.resolve_eidos_tag("1.2.0")
                else:
                    self.assertEqual(planner.resolve_eidos_tag("1.2.0"), expected)

    def test_git_failure(self):
        for error in (OSError(), subprocess.TimeoutExpired("git", 20)):
            with patch("subprocess.run", side_effect=error), self.assertRaises(planner.ScanError):
                planner.resolve_eidos_tag("1.2.0")

    def test_metadata_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "eidos").mkdir()
            recipe = ROOT / "eidos/meta.yaml"
            (root / "eidos/meta.yaml").write_text(recipe.read_text())
            path = root / "eidos/release.json"
            original = (ROOT / "eidos/release.json").read_text()
            path.write_text(original)
            dep = SimpleNamespace(id="eidos", source_type="github-tags", repository="i4Ds/eidos", version_variables=("EIDOS_VERSION",), recipes=("eidos/meta.yaml",), version_file="eidos/release.json")
            registry = SimpleNamespace(settings=SimpleNamespace(http_timeout_seconds=20))
            with patch.object(planner, "resolve_eidos_tag", return_value="a"*40):
                changes = planner.prepare_eidos_changes(root, registry, dep, {"EIDOS_VERSION":"1.1.0"}, "1.2.0")
            self.assertEqual(set(changes), {path})
            self.assertEqual(json.loads(changes[path][1]), {"version":"1.2.0", "build_number":0, "git_rev":"a"*40})
            self.assertEqual(path.read_text(), original)
            planner.validate_upgrade_paths(root, changes)
            for field, value in (("git_rev", "bad"), ("build_number", True), ("version", "0.0.1")):
                data = json.loads(original)
                data[field] = value
                path.write_text(json.dumps(data))
                with patch.object(planner, "resolve_eidos_tag") as resolve, self.assertRaises(planner.ScanError):
                    planner.prepare_eidos_changes(root, registry, dep, {"EIDOS_VERSION":"1.1.0"}, "1.2.0")
                resolve.assert_not_called()

    def test_workflow(self):
        jobs = yaml.safe_load((ROOT / ".github/workflows/validate_dependency_pr.yml").read_text())["jobs"]
        config = jobs["validate-eidos"]["with"]
        self.assertIs(config["publish"], False)
        self.assertEqual(config["buildNumber"], "0")
        self.assertEqual(config["checkoutRef"], "${{ needs.detect-changes.outputs.head }}")
        self.assertEqual(config["versionOverride"], "${{ needs.detect-changes.outputs.eidos_version }}")
        build = yaml.safe_load((ROOT / ".github/workflows/build_eidos.yml").read_text())
        self.assertIs(build["jobs"]["eidos"]["with"]["isNative"], False)
        self.assertIn("noarch: python", (ROOT / "eidos/meta.yaml").read_text())

    def test_override(self):
        import re
        import os
        jobs = yaml.safe_load((ROOT / ".github/workflows/build_base.yml").read_text())["jobs"]
        script = next(step["run"] for job in jobs.values() for step in job.get("steps", []) if "read_release_version()" in step.get("run", ""))
        reader = re.search(r"(?ms)^read_release_version\(\) \{\n.*?^\}", script).group()
        branch = re.search(r"(?ms)^[ \t]*EIDOS_VERSION\)\n(.*?)^[ \t]*;;", script).group(1)
        for override, expected in (("", 0), ("1.1.0", 0), ("1.2.0", 1)):
            env = dict(os.environ, EIDOS_VERSION_OVERRIDE=override)
            result = subprocess.run(["bash", "-c", reader + '\nEIDOS_VERSION=$(read_release_version "$1") || exit 1\n' + branch, "test", str(ROOT / "eidos/release.json")], env=env, capture_output=True)
            self.assertEqual(result.returncode, expected)
