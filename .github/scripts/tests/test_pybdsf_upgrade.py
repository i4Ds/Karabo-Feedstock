import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_dependency_upgrade as planner

class TestPyBDSF(unittest.TestCase):
    def test_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "pybdsf/release.json"
            path.parent.mkdir()
            original = json.dumps(dict(version="1.14.1", build_number=2, sha256="a"*64))
            path.write_text(original)
            recipe = Path(__file__).resolve().parents[3] / "pybdsf/meta.yaml"
            (path.parent / "meta.yaml").write_text(recipe.read_text())
            dep = SimpleNamespace(id="pybdsf", source_type="pypi", package="bdsf", version_variables=("PYBDSF_VERSION",), recipes=("pybdsf/meta.yaml",), version_file="pybdsf/release.json")
            registry = SimpleNamespace(settings=SimpleNamespace(http_timeout_seconds=20))
            with patch.object(planner, "get_pypi_sdist", return_value=("bdsf-1.14.2.tar.gz", "b"*64)):
                changes = planner.prepare_pybdsf_changes(root, registry, dep, {"PYBDSF_VERSION":"1.14.1"}, "1.14.2")
                self.assertEqual(set(changes), {path})
                self.assertEqual(json.loads(changes[path][1]), dict(version="1.14.2", build_number=0, sha256="b"*64))
                self.assertEqual(path.read_text(), original)
                planner.validate_upgrade_paths(root, changes)
                dep.package = "wrong"
                with self.assertRaises(planner.ScanError):
                    planner.prepare_pybdsf_changes(root, registry, dep, {"PYBDSF_VERSION":"1.14.1"}, "1.14.2")
