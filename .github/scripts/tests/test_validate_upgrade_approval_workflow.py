"""Regression tests for dependency upgrade approval automation."""

import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/validate-upgrade-approval.yml"


class TestUpgradeApprovalWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)
        cls.jobs = cls.workflow["jobs"]
        cls.job = cls.jobs["create-draft-pr"]

    def get_step(self, name):
        matches = [
            step for step in self.job["steps"]
            if step.get("name") == name
        ]
        self.assertEqual(len(matches), 1, name)
        return matches[0]

    def test_dependency_allowlist(self):
        condition = self.job["if"]
        self.assertIn('["aotools","idg","everybeam","wsclean","oskar","fftw","finufft","pybdsf","ska-telmodel","ska-sdp-datamodels"]', condition)
        self.assertIn("needs.verify.outputs.dependency", condition)
        self.assertIn("needs.verify.result == 'success'", condition)

    def test_approval_revalidation(self):
        step = self.get_step("Revalidate approval")
        self.assertIn("--verify-only", step["run"])
        self.assertIn(
            '--expected-dependency "$EXPECTED_DEPENDENCY"',
            step["run"],
        )
        self.assertIn(
            '--expected-version "$EXPECTED_VERSION"',
            step["run"],
        )
        self.assertIn(
            '--expected-issue-number "$EXPECTED_ISSUE_NUMBER"',
            step["run"],
        )

        for key in (
            "EXPECTED_DEPENDENCY",
            "EXPECTED_VERSION",
            "EXPECTED_ISSUE_NUMBER",
        ):
            self.assertIn(key, step["env"])

    def test_dependency_specific_preparation(self):
        step = self.get_step("Prepare dependency upgrade")
        self.assertIn(
            "prepare_dependency_upgrade.py",
            step["run"],
        )
        self.assertIn('--dependency "$DEPENDENCY"', step["run"])
        self.assertIn('--version "$VERSION"', step["run"])
        self.assertIn("--write", step["run"])

    def test_exact_changed_file_allowlist(self):
        step = self.get_step("Verify changed files")
        script = step["run"]

        self.assertIn('case "$DEPENDENCY" in', script)

        self.assertIsNotNone(
            re.search(
                r"aotools\).*?aotools/meta\.yaml.*?"
                r"aotools/release\.json",
                script,
                re.DOTALL,
            )
        )
        self.assertIsNotNone(
            re.search(
                r"idg\).*?expected='idg/release\.json'",
                script,
                re.DOTALL,
            )
        )
        self.assertIn(
            '[[ "$actual" == "$expected" ]]',
            script,
        )

    def test_oskar_release_metadata_allowlist(self):
        step = self.get_step("Verify changed files")
        script = step["run"]

        self.assertRegex(
            script,
            r"oskar\)\s+expected='oskar/release\.json'",
        )
        self.assertNotIn(
            ".github/workflows/build_base.yml",
            script,
        )

    def test_workflow_modifications_are_blocked(self):
        step = self.get_step("Verify changed files")
        script = step["run"]

        self.assertIn(
            r"grep -q '^\.github/workflows/'",
            script,
        )
        self.assertRegex(
            script,
            r"Automated upgrades must not modify workflow files."
            r'"\s+exit 1',
        )

        steps = self.job["steps"]
        self.assertLess(
            steps.index(step),
            steps.index(self.get_step("Create Draft PR")),
        )

        config = self.get_step("Create Draft PR")["with"]

        for allowed_path in config["add-paths"].splitlines():
            self.assertFalse(
                allowed_path.strip().startswith(".github/workflows/")
            )

    def test_draft_pr_configuration(self):
        step = self.get_step("Create Draft PR")
        self.assertEqual(
            step["uses"],
            "peter-evans/create-pull-request@v7",
        )

        config = step["with"]
        self.assertIs(config["draft"], True)
        self.assertIs(config["delete-branch"], False)
        self.assertEqual(config["base"], "main")

        self.assertIn(
            "needs.verify.outputs.dependency",
            config["branch"],
        )

        allowed = set(config["add-paths"].splitlines())
        allowed.discard("")

        self.assertEqual(
            allowed,
            {
                "aotools/meta.yaml",
                "aotools/release.json",
                "idg/release.json",
                "everybeam/meta.yaml",
                "everybeam/release.json",
                "wsclean/release.json",
                "oskar/release.json",
                "fftw/release.json",
                "finufft/release.json",
                "pybdsf/release.json",
                "ska-telmodel/release.json",
                "ska-sdp-datamodels/release.json",
            },
        )

    def test_centralized_ci_dispatch(self):
        step = self.get_step("Trigger centralized CI")
        self.assertIn(
            "steps.create_pr.outputs.pull-request-number",
            step["if"],
        )
        self.assertIn(
            "gh workflow run validate_dependency_pr.yml",
            step["run"],
        )
        self.assertIn('-f pr_number="$PR_NUMBER"', step["run"])

    def test_no_automatic_merge(self):
        self.assertNotRegex(
            self.source,
            r"\bgh\s+pr\s+merge\b",
        )


if __name__ == "__main__":
    unittest.main()
