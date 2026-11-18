"""Regression tests for automated EveryBeam upgrade preparation."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dependency_scan import ScanError
from prepare_dependency_upgrade import (
    prepare_everybeam_changes,
    resolve_everybeam_tag,
)


OLD_SHA = "49b61d96785f700cbc200b220478a66b4fedf2d0"
NEW_SHA = "dd80f46b45fd658c8008a41fe1f8b610b9a2ebb8"


class TestEveryBeamTag(unittest.TestCase):
    @patch("subprocess.run")
    def test_valid_upstream_tag(self, run):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout=f"{NEW_SHA}\trefs/tags/v0.9.1\n",
        )

        self.assertEqual(resolve_everybeam_tag("0.9.1"), NEW_SHA)
        args = run.call_args.args[0]
        self.assertIn("refs/tags/v0.9.1", args)

    @patch("subprocess.run")
    def test_missing_upstream_tag(self, run):
        run.return_value = SimpleNamespace(
            returncode=2,
            stdout="",
        )

        with self.assertRaises(ScanError):
            resolve_everybeam_tag("0.9.1")

    @patch("subprocess.run")
    def test_rejects_unexpected_tag_response(self, run):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout=f"{NEW_SHA}\trefs/tags/v0.9.2\n",
        )

        with self.assertRaises(ScanError):
            resolve_everybeam_tag("0.9.1")


class TestEveryBeamPreparation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        directory = self.root / "everybeam"
        directory.mkdir()

        self.recipe_path = directory / "meta.yaml"
        self.release_path = directory / "release.json"

        self.recipe = (
            "package:\n"
            "  name: everybeam\n"
            "source:\n"
            "  git_url: https://git.astron.nl/RD/EveryBeam.git\n"
            f"  git_rev: {OLD_SHA}\n"
            "build:\n"
            "  number: 0\n"
        )
        self.recipe_path.write_text(self.recipe, encoding="utf-8")
        self.release_path.write_text(
            json.dumps({"version": "0.8.0", "build_number": 0}) + "\n",
            encoding="utf-8",
        )

        self.registry = SimpleNamespace(
            settings=SimpleNamespace(http_timeout_seconds=20)
        )
        self.dependency = SimpleNamespace(
            id="everybeam",
            source_type="gitlab-tags",
            version_variables=["EVERYBEAM_VERSION"],
            recipes=["everybeam/meta.yaml"],
            version_file="everybeam/release.json",
        )

    @patch(
        "prepare_dependency_upgrade.resolve_everybeam_tag",
        return_value=NEW_SHA,
    )
    def test_prepares_exactly_two_files(self, resolve):
        changes = prepare_everybeam_changes(
            self.root,
            self.registry,
            self.dependency,
            {"EVERYBEAM_VERSION": "0.8.0"},
            "0.9.1",
        )

        self.assertEqual(
            set(changes),
            {self.recipe_path, self.release_path},
        )
        self.assertIn(NEW_SHA, changes[self.recipe_path][1])
        self.assertNotIn(OLD_SHA, changes[self.recipe_path][1])

        metadata = json.loads(changes[self.release_path][1])
        self.assertEqual(metadata["version"], "0.9.1")
        self.assertEqual(metadata["build_number"], 0)

        # Preparing changes must not write them.
        self.assertEqual(
            self.recipe_path.read_text(encoding="utf-8"),
            self.recipe,
        )
        self.assertEqual(
            json.loads(self.release_path.read_text(encoding="utf-8"))["version"],
            "0.8.0",
        )
        resolve.assert_called_once_with("0.9.1", 20)

    @patch("prepare_dependency_upgrade.resolve_everybeam_tag")
    def test_rejects_unexpected_repository(self, resolve):
        self.recipe_path.write_text(
            self.recipe.replace(
                "https://git.astron.nl/RD/EveryBeam.git",
                "https://example.com/other.git",
            ),
            encoding="utf-8",
        )

        with self.assertRaises(ScanError):
            prepare_everybeam_changes(
                self.root,
                self.registry,
                self.dependency,
                {"EVERYBEAM_VERSION": "0.8.0"},
                "0.9.1",
            )

        resolve.assert_not_called()

    @patch("prepare_dependency_upgrade.resolve_everybeam_tag")
    def test_rejects_mismatched_release_version(self, resolve):
        self.release_path.write_text(
            '{"version": "0.7.0", "build_number": 0}\n',
            encoding="utf-8",
        )

        with self.assertRaises(ScanError):
            prepare_everybeam_changes(
                self.root,
                self.registry,
                self.dependency,
                {"EVERYBEAM_VERSION": "0.8.0"},
                "0.9.1",
            )

        resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
