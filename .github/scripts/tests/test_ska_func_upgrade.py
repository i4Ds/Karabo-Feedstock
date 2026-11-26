"""Tests for automated SKA SDP Func Native upgrades."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_dependency_upgrade as planner


class TestSKAFuncUpgrade(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        self.variable = "SKA_SDP_FUNC_VERSION"
        self.target = "1.2.2"

        self.registry = SimpleNamespace(
            settings=SimpleNamespace(http_timeout_seconds=20)
        )

        self.dependency = SimpleNamespace(
            id="ska-sdp-func",
            source_type="gitlab-tags",
            project="ska-telescope/sdp/ska-sdp-func",
            base_url="https://gitlab.com",
            version_variables=(self.variable,),
            recipes=("ska-sdp-func/meta.yaml",),
            version_file="ska-sdp-func/release.json",
        )

        self.current = {self.variable: "1.0.0"}

        self.path = self.root / self.dependency.version_file
        self.path.parent.mkdir(parents=True)

        self.original = json.dumps({
            "version": "1.0.0",
            "build_number": 1,
        })

        self.path.write_text(self.original)

        self.recipe = self.path.parent / "meta.yaml"
        self.recipe.write_text(
            "source:\n"
            "  git_url: https://gitlab.com/"
            "ska-telescope/sdp/ska-sdp-func.git\n"
            "  git_tag: {{ SKA_SDP_FUNC_VERSION }}\n"
        )

    def prepare(self):
        return planner.prepare_ska_func_changes(
            self.root,
            self.registry,
            self.dependency,
            self.current,
            self.target,
        )

    def valid_tag(self):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=f"{'a' * 40}\trefs/tags/{self.target}\n",
            stderr="",
        )

    def test_metadata_only(self):
        with patch(
            "subprocess.run",
            return_value=self.valid_tag(),
        ) as git:
            changes = self.prepare()

        self.assertEqual(set(changes), {self.path})

        self.assertEqual(
            json.loads(changes[self.path][1]),
            {
                "version": self.target,
                "build_number": 0,
            },
        )

        self.assertEqual(
            self.path.read_text(), self.original
        )

        planner.validate_upgrade_paths(self.root, changes)

        git.assert_called_once_with(
            [
                "git", "ls-remote", "--exit-code", "--refs",
                "--tags",
                "https://gitlab.com/"
                "ska-telescope/sdp/ska-sdp-func.git",
                f"refs/tags/{self.target}",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_missing_tag(self):
        result = self.valid_tag()
        result.returncode = 2
        result.stdout = ""

        with patch("subprocess.run", return_value=result):
            with self.assertRaises(planner.ScanError):
                self.prepare()

    def test_invalid_git_response(self):
        responses = (
            "",
            f"invalid-hash\trefs/tags/{self.target}\n",
            f"{'a' * 40}\trefs/tags/0.0.1\n",
            f"{'a' * 40}\trefs/tags/{self.target} extra\n",
            self.valid_tag().stdout * 2,
        )

        for response in responses:
            with self.subTest(response=response):
                result = self.valid_tag()
                result.stdout = response

                with patch(
                    "subprocess.run", return_value=result
                ):
                    with self.assertRaises(planner.ScanError):
                        self.prepare()

                self.assertEqual(
                    self.path.read_text(), self.original
                )

    def test_git_timeout(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 20),
        ):
            with self.assertRaises(planner.ScanError):
                self.prepare()

    def test_git_execution_error(self):
        with patch(
            "subprocess.run",
            side_effect=OSError("Git unavailable"),
        ):
            with self.assertRaises(planner.ScanError):
                self.prepare()

    def test_invalid_registry_configuration(self):
        invalid_values = {
            "id": "wrong-package",
            "source_type": "pypi",
            "project": "wrong/project",
            "base_url": "https://example.com",
            "version_variables": ("WRONG_VERSION",),
            "recipes": ("wrong/meta.yaml",),
            "version_file": "wrong/release.json",
        }

        for key, value in invalid_values.items():
            with self.subTest(field=key):
                with patch.object(
                    self.dependency, key, value
                ), patch("subprocess.run") as git:
                    with self.assertRaises(planner.ScanError):
                        self.prepare()

                    git.assert_not_called()

    def test_invalid_recipe_source(self):
        self.recipe.write_text(
            "source:\n"
            "  git_url: https://example.com/wrong.git\n"
            "  git_tag: {{ SKA_SDP_FUNC_VERSION }}\n"
        )

        with patch("subprocess.run") as git:
            with self.assertRaises(planner.ScanError):
                self.prepare()

            git.assert_not_called()

    def test_invalid_target_version(self):
        for target in (
            "invalid-version",
            "1.2",
            "1x2x2",
            "v1.2.2",
            "1.2.2rc1",
        ):
            with self.subTest(target=target):
                self.target = target

                with patch("subprocess.run") as git:
                    with self.assertRaises(planner.ScanError):
                        self.prepare()

                    git.assert_not_called()

    def test_invalid_release_metadata(self):
        invalid_metadata = (
            "invalid-json",
            "[]",
            '{"version": "1.0.0"}',
            '{"version": "1.0.0", "build_number": -1}',
            '{"version": "1.0.0", "build_number": true}',
            '{"version": "1.0.0", "build_number": "1"}',
            '{"build_number": 1}',
        )

        for metadata in invalid_metadata:
            with self.subTest(metadata=metadata):
                self.path.write_text(metadata)

                with patch("subprocess.run") as git:
                    with self.assertRaises(planner.ScanError):
                        self.prepare()

                    git.assert_not_called()

    def test_current_version_mismatch(self):
        self.current[self.variable] = "0.9.0"

        with patch("subprocess.run") as git:
            with self.assertRaises(planner.ScanError):
                self.prepare()

            git.assert_not_called()

    def test_native_upgrade_preserves_python_metadata(self):
        python_metadata = (
            self.root / "ska-sdp-func-python/release.json"
        )
        python_metadata.parent.mkdir(parents=True)

        original = json.dumps({
            "version": "1.0.0",
            "build_number": 1,
            "sha256": "a" * 64,
        })

        python_metadata.write_text(original)
        original_recipe = self.recipe.read_text()

        with patch(
            "subprocess.run",
            return_value=self.valid_tag(),
        ):
            changes = self.prepare()

        self.assertEqual(set(changes), {self.path})

        planner.validate_upgrade_paths(self.root, changes)
        planner.apply_changes(changes)

        self.assertEqual(
            python_metadata.read_text(), original
        )
        self.assertEqual(
            self.recipe.read_text(), original_recipe
        )
        self.assertEqual(
            json.loads(self.path.read_text()),
            {
                "version": self.target,
                "build_number": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
