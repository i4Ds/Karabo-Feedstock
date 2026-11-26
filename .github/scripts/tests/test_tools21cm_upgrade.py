"""Tests for automated tools21cm upgrades."""

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


class TestTools21cmUpgrade(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        self.variable = "TOOLS_21CM_VERSION"
        self.target = "2.4.4"

        self.registry = SimpleNamespace(
            settings=SimpleNamespace(http_timeout_seconds=20)
        )

        self.dependency = SimpleNamespace(
            id="tools21cm",
            source_type="github-tags",
            repository="sambit-giri/tools21cm",
            version_variables=(self.variable,),
            recipes=("tools21cm/meta.yaml",),
            version_file="tools21cm/release.json",
        )

        self.current = {self.variable: "2.4.3"}

        self.path = self.root / self.dependency.version_file
        self.path.parent.mkdir(parents=True)

        self.original = json.dumps({
            "version": "2.4.3",
            "build_number": 2,
        })

        self.path.write_text(self.original)

        self.recipe = self.path.parent / "meta.yaml"

        self.original_recipe = (
            "package:\n"
            "  version: {{ TOOLS_21CM_VERSION_ALT }}\n"
            "source:\n"
            '  git_url: "https://github.com/sambit-giri/tools21cm"\n'
            '  git_rev: "v{{ TOOLS_21CM_VERSION }}"\n'
        )

        self.recipe.write_text(self.original_recipe)

    def prepare(self):
        return planner.prepare_tools21cm_changes(
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
            stdout=f"{'a' * 40}\trefs/tags/v{self.target}\n",
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

        self.assertEqual(self.path.read_text(), self.original)

        planner.validate_upgrade_paths(self.root, changes)

        git.assert_called_once_with(
            [
                "git", "ls-remote", "--exit-code", "--refs",
                "--tags",
                "https://github.com/sambit-giri/tools21cm.git",
                f"refs/tags/v{self.target}",
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

        self.assertEqual(self.path.read_text(), self.original)

    def test_invalid_git_response(self):
        responses = (
            "",
            f"invalid-hash\trefs/tags/v{self.target}\n",
            f"{'a' * 40}\trefs/tags/v0.0.1\n",
            f"{'a' * 40}\trefs/heads/main\n",
            f"{'a' * 40}\trefs/tags/{self.target}\n",
            f"{'a' * 40}\trefs/tags/v{self.target} extra\n",
            f"{'a' * 41}\trefs/tags/v{self.target}\n",
            self.valid_tag().stdout * 2,
        )

        for response in responses:
            with self.subTest(response=response):
                result = self.valid_tag()
                result.stdout = response

                with patch("subprocess.run", return_value=result):
                    with self.assertRaises(planner.ScanError):
                        self.prepare()

                self.assertEqual(self.path.read_text(), self.original)

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
            "repository": "wrong/project",
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

    def test_invalid_recipe_configuration(self):
        invalid_recipes = (
            self.original_recipe.replace(
                "sambit-giri/tools21cm", "wrong/project"
            ),
            self.original_recipe.replace(
                "v{{ TOOLS_21CM_VERSION }}", "main"
            ),
            self.original_recipe.replace(
                "v{{ TOOLS_21CM_VERSION }}", "v2.4.3"
            ),
            self.original_recipe.replace(
                "TOOLS_21CM_VERSION_ALT", "WRONG_VERSION"
            ),
            self.original_recipe
            + '  git_rev: "main"\n',
        )

        for recipe in invalid_recipes:
            with self.subTest(recipe=recipe):
                self.recipe.write_text(recipe)

                with patch("subprocess.run") as git:
                    with self.assertRaises(planner.ScanError):
                        self.prepare()

                    git.assert_not_called()

    def test_invalid_target_version(self):
        for target in (
            "invalid-version",
            "2.5",
            "2x5x0",
            "v2.5.0",
            "2.5.0rc1",
            "2.5.0.post1",
            "2.5.0.1",
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
            '{"version": "2.4.3"}',
            '{"version": "2.4.3", "build_number": -1}',
            '{"version": "2.4.3", "build_number": true}',
            '{"version": "2.4.3", "build_number": "2"}',
            '{"build_number": 2}',
        )

        for metadata in invalid_metadata:
            with self.subTest(metadata=metadata):
                self.path.write_text(metadata)

                with patch("subprocess.run") as git:
                    with self.assertRaises(planner.ScanError):
                        self.prepare()

                    git.assert_not_called()

    def test_current_version_mismatch(self):
        self.current[self.variable] = "2.3.9"

        with patch("subprocess.run") as git:
            with self.assertRaises(planner.ScanError):
                self.prepare()

            git.assert_not_called()

    def test_apply_preserves_recipe_and_unrelated_files(self):
        unrelated = self.root / "unrelated.txt"
        unrelated.write_text("unchanged")

        with patch(
            "subprocess.run",
            return_value=self.valid_tag(),
        ):
            changes = self.prepare()

        self.assertEqual(set(changes), {self.path})

        planner.validate_upgrade_paths(self.root, changes)
        planner.apply_changes(changes)

        self.assertEqual(
            json.loads(self.path.read_text()),
            {
                "version": self.target,
                "build_number": 0,
            },
        )

        self.assertEqual(
            self.recipe.read_text(), self.original_recipe
        )

        self.assertEqual(unrelated.read_text(), "unchanged")


class TestTools21cmWorkflow(unittest.TestCase):

    def test_centralized_validation(self):
        import yaml

        root = Path(__file__).resolve().parents[3]

        workflow = yaml.safe_load(
            (
                root
                / ".github/workflows/validate_dependency_pr.yml"
            ).read_text(encoding="utf-8")
        )

        jobs = workflow["jobs"]
        detect = jobs["detect-changes"]
        job = jobs["validate-tools21cm"]

        self.assertEqual(
            detect["outputs"]["tools21cm_version"],
            "${{ steps.tools21cm_version.outputs.version }}",
        )

        steps = [
            step for step in detect["steps"]
            if step.get("id") == "tools21cm_version"
        ]

        self.assertEqual(len(steps), 1)

        self.assertIn(
            "tools21cm_release.py",
            steps[0]["run"],
        )

        self.assertIn(
            "dependencies).tools21cm",
            steps[0]["if"],
        )

        self.assertEqual(
            job["uses"],
            "./.github/workflows/build_tools21cm.yml",
        )

        self.assertEqual(job["needs"], "detect-changes")

        self.assertIs(job["with"]["publish"], False)

        self.assertEqual(
            job["with"]["checkoutRef"],
            "${{ needs.detect-changes.outputs.head }}",
        )

        self.assertEqual(
            job["with"]["versionOverride"],
            "${{ needs.detect-changes.outputs.tools21cm_version }}",
        )

        self.assertEqual(
            job["with"]["buildNumber"],
            "0",
        )

        self.assertNotIn("secrets", job)

    def test_reusable_build_workflow(self):
        import yaml

        root = Path(__file__).resolve().parents[3]

        workflow = yaml.safe_load(
            (
                root
                / ".github/workflows/build_tools21cm.yml"
            ).read_text(encoding="utf-8")
        )

        triggers = workflow.get("on", workflow.get(True))

        for trigger in ("workflow_dispatch", "workflow_call"):
            with self.subTest(trigger=trigger):
                inputs = triggers[trigger]["inputs"]

                self.assertIs(
                    inputs["publish"]["default"],
                    True,
                )

                self.assertIn("checkoutRef", inputs)
                self.assertIn("versionOverride", inputs)

        jobs = [
            job for job in workflow["jobs"].values()
            if job.get("uses")
            == "./.github/workflows/build_base.yml"
        ]

        self.assertEqual(len(jobs), 1)

        config = jobs[0]["with"]

        self.assertEqual(
            config["checkoutRef"],
            "${{ inputs.checkoutRef }}",
        )

        self.assertEqual(
            config["versionOverride"],
            "${{ inputs.versionOverride }}",
        )

        self.assertEqual(
            config["publish"],
            "${{ inputs.publish }}",
        )

        self.assertIs(config["buildArm"], True)
        self.assertIs(config["isNative"], True)



class TestTools21cmVersionOverride(unittest.TestCase):

    def setUp(self):
        import re
        import yaml

        root = Path(__file__).resolve().parents[3]

        workflow = yaml.safe_load(
            (
                root / ".github/workflows/build_base.yml"
            ).read_text(encoding="utf-8")
        )

        scripts = [
            step["run"]
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if "read_release_version()" in step.get("run", "")
            and "TOOLS_21CM_VERSION)" in step.get("run", "")
        ]

        self.assertEqual(len(scripts), 1)

        script = scripts[0]

        reader = re.search(
            r"(?ms)^read_release_version\(\) \{\n.*?^\}",
            script,
        )

        self.assertIsNotNone(reader)

        branch = re.search(
            r"(?ms)^[ \t]*TOOLS_21CM_VERSION\)\n"
            r"(.*?)^[ \t]*;;",
            script,
        )

        self.assertIsNotNone(branch)

        self.reader = reader.group(0)
        self.branch = branch.group(1)

        expression = "${{ inputs.versionOverride || '' }}"

        self.assertEqual(self.branch.count(expression), 1)

        self.branch = self.branch.replace(
            expression,
            "${TEST_VERSION_OVERRIDE:-}",
            1,
        )

    def run_resolution(self, override, metadata):
        import os
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory) / "release.json"
            release.write_text(metadata, encoding="utf-8")

            script = (
                "set -e\n"
                + self.reader
                + '\nTOOLS_21CM_VERSION='
                + '$(read_release_version "$1") || exit 1\n'
                + self.branch
                + '\nprintf "resolved=%s\\n" "$TOOLS_21CM_VERSION"\n'
            )

            env = os.environ.copy()
            env["TEST_VERSION_OVERRIDE"] = override

            return subprocess.run(
                ["bash", "-c", script, "test", str(release)],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

    def test_empty_override_uses_release_metadata(self):
        result = self.run_resolution(
            "",
            '{"version": "2.4.3", "build_number": 0}',
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "resolved=2.4.3")

    def test_matching_override_is_accepted(self):
        result = self.run_resolution(
            "2.4.3",
            '{"version": "2.4.3", "build_number": 0}',
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "resolved=2.4.3")

    def test_conflicting_override_is_rejected(self):
        result = self.run_resolution(
            "2.4.4",
            '{"version": "2.4.3", "build_number": 0}',
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "tools21cm versionOverride does not match release.json",
            result.stdout,
        )
        self.assertNotIn("resolved=", result.stdout)

    def test_invalid_release_metadata_is_rejected(self):
        for metadata in (
            '{"version": "invalid"}',
            '{"version": "2.4"}',
            '{"version": "2.4.3rc1"}',
            '{"version": 243}',
            '{"version": null}',
            '{}',
            'invalid json',
        ):
            with self.subTest(metadata=metadata):
                result = self.run_resolution("", metadata)

                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("resolved=", result.stdout)


if __name__ == "__main__":
    unittest.main()
