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

class TestSeqfileUpgrade(unittest.TestCase):
    def test_metadata_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "seqfile").mkdir()
            recipe = ROOT / "seqfile/meta.yaml"
            (root / "seqfile/meta.yaml").write_text(recipe.read_text())
            path = root / "seqfile/release.json"
            original = (ROOT / "seqfile/release.json").read_text()
            path.write_text(original)
            dep = SimpleNamespace(id="seqfile", source_type="pypi", package="seqfile", version_variables=("SEQFILE_VERSION",), recipes=("seqfile/meta.yaml",), version_file="seqfile/release.json")
            registry = SimpleNamespace(settings=SimpleNamespace(http_timeout_seconds=20))
            with patch.object(planner, "get_pypi_sdist", return_value=("seqfile-0.2.1.tar.gz", "a"*64)):
                changes = planner.prepare_seqfile_changes(root, registry, dep, {"SEQFILE_VERSION":"0.2.0"}, "0.2.1")
            self.assertEqual(set(changes), {path})
            self.assertEqual(json.loads(changes[path][1]), {"version":"0.2.1", "build_number":0, "sha256":"a"*64})
            self.assertEqual(path.read_text(), original)
            planner.validate_upgrade_paths(root, changes)
            for field, value in (("sha256", "bad"), ("build_number", True), ("version", "0.0.1")):
                data = json.loads(original)
                data[field] = value
                path.write_text(json.dumps(data))
                with patch.object(planner, "get_pypi_sdist") as resolve, self.assertRaises(planner.ScanError):
                    planner.prepare_seqfile_changes(root, registry, dep, {"SEQFILE_VERSION":"0.2.0"}, "0.2.1")
                resolve.assert_not_called()

    def test_workflow(self):
        jobs = yaml.safe_load((ROOT / ".github/workflows/validate_dependency_pr.yml").read_text())["jobs"]
        config = jobs["validate-seqfile"]["with"]
        self.assertIs(config["publish"], False)
        self.assertEqual(config["buildNumber"], "0")
        self.assertEqual(config["checkoutRef"], "${{ needs.detect-changes.outputs.head }}")
        self.assertEqual(config["versionOverride"], "${{ needs.detect-changes.outputs.seqfile_version }}")
        build = yaml.safe_load((ROOT / ".github/workflows/build_seqfile.yml").read_text())
        self.assertIs(build["jobs"]["SeqFile"]["with"]["isNative"], True)
        self.assertNotIn("noarch: python", (ROOT / "seqfile/meta.yaml").read_text())

    def test_override(self):
        import re
        import os
        jobs = yaml.safe_load((ROOT / ".github/workflows/build_base.yml").read_text())["jobs"]
        script = next(step["run"] for job in jobs.values() for step in job.get("steps", []) if "read_release_version()" in step.get("run", ""))
        reader = re.search(r"(?ms)^read_release_version\(\) \{\n.*?^\}", script).group()
        branch = re.search(r"(?ms)^[ \t]*SEQFILE_VERSION\)\n(.*?)^[ \t]*;;", script).group(1)
        for override, expected in (("", 0), ("0.2.0", 0), ("0.2.1", 1)):
            env = dict(os.environ, SEQFILE_VERSION_OVERRIDE=override)
            result = subprocess.run(["bash", "-c", reader + '\nSEQFILE_VERSION=$(read_release_version "$1") || exit 1\n' + branch, "test", str(ROOT / "seqfile/release.json")], env=env, capture_output=True)
            self.assertEqual(result.returncode, expected)

    def test_pypi_source(self):
        from unittest.mock import Mock
        http = Mock()
        artifact = {"filename": "seqfile-0.2.1.tar.gz", "packagetype": "sdist", "yanked": False, "digests": {"sha256": "a" * 64}}
        http.get_json.return_value = {"urls": [artifact]}
        self.assertEqual(planner.get_pypi_sdist("seqfile", "0.2.1", http), (artifact["filename"], "a" * 64))
        for files in ([], [artifact, artifact], [dict(artifact, yanked=True)], [dict(artifact, filename="other.tar.gz")], [dict(artifact, digests={})]):
            http.get_json.return_value = {"urls": files}
            with self.assertRaises(planner.ScanError):
                planner.get_pypi_sdist("seqfile", "0.2.1", http)
