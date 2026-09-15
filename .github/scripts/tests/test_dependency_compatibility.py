from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / ".github/scripts/dependency_compatibility.py"
SPEC = importlib.util.spec_from_file_location("dependency_compatibility", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
compatibility = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = compatibility
SPEC.loader.exec_module(compatibility)


class CompatibilityRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = compatibility.dependency_scan.load_registry(
            REPO_ROOT / ".github/dependency-scan.toml"
        )

    def test_every_active_dependency_has_a_validation_recipe(self) -> None:
        active = [
            dependency
            for dependency in self.registry.dependencies
            if dependency.compatibility_mode not in {"manual", "retired"}
        ]
        self.assertEqual(len(active), 19)
        for dependency in active:
            with self.subTest(dependency=dependency.id):
                self.assertTrue(dependency.validation_recipe_paths)
                self.assertEqual(
                    len(dependency.validation_recipe_paths),
                    len(dependency.validation_package_names),
                )

    def test_aotools_preflight_targets_python_and_numpy(self) -> None:
        dependency = compatibility.dependency_by_id(self.registry, "aotools")
        results, warnings = compatibility.preflight_checks(
            REPO_ROOT, self.registry, dependency
        )
        self.assertFalse(warnings)
        self.assertFalse(any(result.status == "Failed" for result in results))
        self.assertIn(
            ("Python variant", "Passed", "3.12"),
            [(result.name, result.status, result.detail) for result in results],
        )
        self.assertIn(
            ("NumPy variant", "Passed", "2.2.6"),
            [(result.name, result.status, result.detail) for result in results],
        )

    def test_retired_dependency_cannot_be_validated(self) -> None:
        with self.assertRaisesRegex(
            compatibility.ScanError, "is not automated"
        ):
            compatibility.dependency_by_id(self.registry, "rascil")


class VariantTests(unittest.TestCase):
    def test_parse_variant_values_reads_only_requested_top_level_list(self) -> None:
        content = """python:
  - 3.11
  - 3.12 # supported
numpy:
  - 2.2
"""
        self.assertEqual(
            compatibility.parse_variant_values(content, "python"),
            ("3.11", "3.12"),
        )
        self.assertEqual(
            compatibility.parse_variant_values(content, "numpy"), ("2.2",)
        )


class PlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = compatibility.dependency_scan.load_registry(
            REPO_ROOT / ".github/dependency-scan.toml"
        )

    def test_recipe_change_selects_only_its_dependency(self) -> None:
        with mock.patch.object(
            compatibility, "_run_git", return_value="aotools/meta.yaml\n"
        ):
            selected = compatibility.select_changed_dependencies(
                REPO_ROOT, self.registry, "base", "HEAD"
            )
        self.assertEqual([dependency.id for dependency in selected], ["aotools"])

    def test_build_version_change_selects_matching_dependency(self) -> None:
        current = (
            REPO_ROOT / self.registry.settings.build_base
        ).read_text(encoding="utf-8")
        old = current.replace("AOTOOLS_VERSION: 1.0.7", "AOTOOLS_VERSION: 1.0.6")

        def fake_git(_repo_root: Path, arguments: list[str]) -> str:
            if arguments[:2] == ["diff", "--name-only"]:
                return f"{self.registry.settings.build_base}\n"
            if arguments[0] == "show":
                return old
            raise AssertionError(arguments)

        with mock.patch.object(compatibility, "_run_git", side_effect=fake_git):
            selected = compatibility.select_changed_dependencies(
                REPO_ROOT, self.registry, "base", "HEAD"
            )
        self.assertEqual([dependency.id for dependency in selected], ["aotools"])

    def test_version_change_avoids_duplicate_shared_recipe_build(self) -> None:
        current = (
            REPO_ROOT / self.registry.settings.build_base
        ).read_text(encoding="utf-8")
        old = current.replace("FINUFFT_VERSION: 2.2.0", "FINUFFT_VERSION: 2.1.0")

        def fake_git(_repo_root: Path, arguments: list[str]) -> str:
            if arguments[:2] == ["diff", "--name-only"]:
                return (
                    f"{self.registry.settings.build_base}\n"
                    "finufft/meta.yaml\n"
                )
            if arguments[0] == "show":
                return old
            raise AssertionError(arguments)

        with mock.patch.object(compatibility, "_run_git", side_effect=fake_git):
            selected = compatibility.select_changed_dependencies(
                REPO_ROOT, self.registry, "base", "HEAD"
            )
        self.assertEqual([dependency.id for dependency in selected], ["finufft"])

    def test_matrix_contains_cuda_metadata(self) -> None:
        dependency = compatibility.dependency_by_id(self.registry, "oskar")
        payload = compatibility.matrix_payload([dependency])
        self.assertEqual(payload["include"][0]["dependency"], "oskar")
        self.assertTrue(payload["include"][0]["needs_cuda"])


class ReportTests(unittest.TestCase):
    def test_failed_check_is_visible_and_publication_is_disabled(self) -> None:
        registry = compatibility.dependency_scan.load_registry(
            REPO_ROOT / ".github/dependency-scan.toml"
        )
        dependency = compatibility.dependency_by_id(registry, "aotools")
        report = compatibility.render_report(
            registry,
            dependency,
            [compatibility.CheckResult("Import", "Failed", "boom")],
            [],
        )
        self.assertIn("Overall result: Failed", report)
        self.assertIn("Python: `3.12`", report)
        self.assertIn("NumPy: `2.2.6`", report)
        self.assertIn("Package publication: **disabled**", report)


class SafetyTests(unittest.TestCase):
    def test_build_environment_removes_anaconda_credentials(self) -> None:
        registry = compatibility.dependency_scan.load_registry(
            REPO_ROOT / ".github/dependency-scan.toml"
        )
        with mock.patch.dict(
            compatibility.os.environ,
            {
                "ANACONDA_API_TOKEN": "must-not-reach-the-build",
                "SOME_ANACONDA_SECRET": "must-not-reach-the-build",
                "GITHUB_TOKEN": "must-not-reach-the-build",
                "SSH_AUTH_SOCK": "/tmp/must-not-reach-the-build",
                "UNRELATED_VALUE": "preserved",
            },
        ):
            environment = compatibility._environment_for_build(
                REPO_ROOT, registry
            )
        self.assertNotIn("ANACONDA_API_TOKEN", environment)
        self.assertNotIn("SOME_ANACONDA_SECRET", environment)
        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertNotIn("SSH_AUTH_SOCK", environment)
        self.assertEqual(environment["UNRELATED_VALUE"], "preserved")


if __name__ == "__main__":
    unittest.main()
