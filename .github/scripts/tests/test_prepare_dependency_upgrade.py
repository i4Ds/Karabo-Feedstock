"""Tests for the automated dependency upgrade preparation."""

import os
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
from contextlib import redirect_stdout
import tempfile
from unittest.mock import patch
from unittest.mock import patch
import sys
from pathlib import Path
import json

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from prepare_dependency_upgrade import main

from dependency_scan import ScanError
from prepare_dependency_upgrade import (
    get_pypi_sdist,
    replace_version_variable,
    replace_recipe_sha256,
    update_release_metadata,
    show_diff,
    apply_changes,
    prepare_dependency_version_updates,
)

class TestOskarTag(unittest.TestCase):
    def test_valid_tag(self):
        from prepare_dependency_upgrade import verify_oskar_tag
        result = Mock(returncode=0, stdout="a" * 40 + "  refs/tags/2.13.0\n")
        with patch("subprocess.run", return_value=result):
            verify_oskar_tag("2.13.0")

    def test_missing_tag(self):
        from prepare_dependency_upgrade import verify_oskar_tag
        with patch("subprocess.run", return_value=Mock(returncode=2, stdout="")):
            with self.assertRaises(ScanError):
                verify_oskar_tag("2.13.0")

    def test_prepare_oskar_with_release_metadata(self):
        """Upgrade shared release metadata without modifying workflows."""
        import json
        from prepare_dependency_upgrade import (
            prepare_oskar_changes,
            validate_upgrade_paths,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            (root / ".github/workflows").mkdir(parents=True)
            (root / "oskar").mkdir()
            (root / "oskar-py").mkdir()

            workflow = root / ".github/workflows/build_base.yml"
            workflow_content = "# Shared build workflow\n"
            workflow.write_text(workflow_content, encoding="utf-8")

            release = root / "oskar/release.json"
            release.write_text(
                json.dumps({
                    "version": "2.12.2",
                    "build_number": 1,
                }, indent=2) + "\n",
                encoding="utf-8",
            )

            (root / "oskar/meta.yaml").write_text(
                "git_url: https://github.com/OxfordSKA/OSKAR.git\n"
                "git_tag: {{ OSKAR_VERSION }}\n"
                "- 0001-fix-optional-phase-convention-tag.patch\n"
                "- 0002-fix-strtok-r-with-old-glibc.patch\n",
                encoding="utf-8",
            )

            (root / "oskar-py/meta.yaml").write_text(
                "git_url: https://github.com/OxfordSKA/OSKAR.git\n"
                "git_tag: {{ OSKARPY_VERSION }}\n",
                encoding="utf-8",
            )

            for name in (
                "0001-fix-optional-phase-convention-tag.patch",
                "0002-fix-strtok-r-with-old-glibc.patch",
            ):
                (root / "oskar" / name).write_text(
                    "test patch\n", encoding="utf-8"
                )

            dependency = Mock(
                id="oskar",
                source_type="github-tags",
                version_variables=("OSKAR_VERSION", "OSKARPY_VERSION"),
                recipes=("oskar/meta.yaml", "oskar-py/meta.yaml"),
                version_file="oskar/release.json",
            )

            registry = Mock()
            registry.settings.http_timeout_seconds = 20

            current = {
                "OSKAR_VERSION": "2.12.2",
                "OSKARPY_VERSION": "2.12.2",
            }

            with patch(
                "prepare_dependency_upgrade.verify_oskar_tag"
            ) as verify_tag:
                changes = prepare_oskar_changes(
                    root, registry, dependency, current, "2.13.0"
                )

            verify_tag.assert_called_once_with("2.13.0", 20)

            # Only release metadata may change.
            self.assertEqual(set(changes), {release})
            validate_upgrade_paths(root, changes)

            original, updated = changes[release]

            self.assertEqual(
                json.loads(original),
                {"version": "2.12.2", "build_number": 1},
            )
            self.assertEqual(
                json.loads(updated),
                {"version": "2.13.0", "build_number": 0},
            )

            # Planning must not modify files.
            self.assertEqual(
                release.read_text(encoding="utf-8"), original
            )
            self.assertEqual(
                workflow.read_text(encoding="utf-8"),
                workflow_content,
            )

            # Reject the previous workflow-based architecture.
            dependency.version_file = ""
            with self.assertRaises(ScanError):
                prepare_oskar_changes(
                    root, registry, dependency, current, "2.13.0"
                )

            # Reject mismatched OSKAR and OSKARPY versions.
            dependency.version_file = "oskar/release.json"
            current["OSKARPY_VERSION"] = "2.12.1"

            with self.assertRaises(ScanError):
                prepare_oskar_changes(
                    root, registry, dependency, current, "2.13.0"
                )


class TestUpgradePathProtection(unittest.TestCase):

    def setUp(self):
        from prepare_dependency_upgrade import validate_upgrade_paths

        self.validate = validate_upgrade_paths
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)

        self.root = Path(temporary.name) / "repository"
        self.root.mkdir()

        self.workflow = (
            self.root / ".github/workflows/build_base.yml"
        )
        self.workflow.parent.mkdir(parents=True)
        self.workflow.write_text("original", encoding="utf-8")

        self.release = self.root / "oskar/release.json"
        self.release.parent.mkdir()

        self.change = ("original", "updated")

    def test_allows_release_metadata_and_recipe(self):
        self.validate(
            self.root,
            {
                self.release: self.change,
                self.root / "oskar/meta.yaml": self.change,
            },
        )

    def test_rejects_workflow_modification(self):
        with self.assertRaisesRegex(
            ScanError, "must not modify workflow files"
        ):
            self.validate(
                self.root,
                {self.workflow: self.change},
            )

    def test_rejects_new_workflow_file(self):
        with self.assertRaises(ScanError):
            self.validate(
                self.root,
                {
                    self.workflow.parent / "new.yml": self.change,
                },
            )

    def test_rejects_path_outside_repository(self):
        outside = self.root.parent / "outside.json"

        with self.assertRaisesRegex(
            ScanError, "outside the repository"
        ):
            self.validate(
                self.root,
                {outside: self.change},
            )

    def test_rejects_symlink_to_workflow(self):
        self.release.symlink_to(self.workflow)

        with self.assertRaisesRegex(
            ScanError, "must not modify workflow files"
        ):
            self.validate(
                self.root,
                {self.release: self.change},
            )

    def test_rejects_mixed_allowed_and_protected_changes(self):
        with self.assertRaises(ScanError):
            self.validate(
                self.root,
                {
                    self.release: self.change,
                    self.workflow: self.change,
                },
            )


class TestPyPISdist(unittest.TestCase):

    def test_valid_sdist(self):
        """Extract the correct filename and SHA256 from PyPI metadata."""

        expected_sha256 = (
            "841cd697e3a534ea99ee89e9ed7ac7ec6"
            "bb1057a5ea2454a91f501197141235b"
        )

        http = Mock()
        http.get_json.return_value = {
            "urls": [
                {
                    "packagetype": "sdist",
                    "filename": "aotools-1.0.8.tar.gz",
                    "yanked": False,
                    "digests": {
                        "sha256": expected_sha256,
                    },
                }
            ]
        }

        filename, sha256 = get_pypi_sdist(
            "aotools",
            "1.0.8",
            http,
        )

        self.assertEqual(filename, "aotools-1.0.8.tar.gz")
        self.assertEqual(sha256, expected_sha256)

        http.get_json.assert_called_once_with(
            "https://pypi.org/pypi/aotools/1.0.8/json"
        )

    def test_yanked_sdist_is_rejected(self):
        """Reject source archives that PyPI has yanked."""

        http = Mock()
        http.get_json.return_value = {
            "urls": [
                {
                    "packagetype": "sdist",
                    "filename": "aotools-1.0.9.tar.gz",
                    "yanked": True,
                    "digests": {
                        "sha256": "a" * 64,
                    },
                }
            ]
        }

        with self.assertRaisesRegex(
            ScanError,
            "Expected exactly one non-yanked source archive",
        ):
            get_pypi_sdist("aotools", "1.0.9", http)


    def test_missing_sdist_is_rejected(self):
        """Reject releases without the expected source archive."""

        http = Mock()
        http.get_json.return_value = {
            "urls": [
                {
                    "packagetype": "bdist_wheel",
                    "filename": "aotools-1.0.9-py3-none-any.whl",
                    "yanked": False,
                    "digests": {
                        "sha256": "a" * 64,
                    },
                }
            ]
        }

        with self.assertRaisesRegex(
            ScanError,
            "Expected exactly one non-yanked source archive",
        ):
            get_pypi_sdist("aotools", "1.0.9", http)

    def test_invalid_sha256_is_rejected(self):
        """Reject source archives with invalid SHA256 metadata."""

        http = Mock()
        http.get_json.return_value = {
            "urls": [
                {
                    "packagetype": "sdist",
                    "filename": "aotools-1.0.9.tar.gz",
                    "yanked": False,
                    "digests": {
                        "sha256": "invalid-checksum",
                    },
                }
            ]
        }

        with self.assertRaisesRegex(
            ScanError,
            "Missing or invalid SHA256 checksum",
        ):
            get_pypi_sdist("aotools", "1.0.9", http)

class TestVersionReplacement(unittest.TestCase):

    def test_replace_aotools_version(self):
        """Update AOtools without changing other dependencies."""

        original = (
            "  AOTOOLS_VERSION: 1.0.8\n"
            "  SEQFILE_VERSION: 0.2.0\n"
            "  EIDOS_VERSION: 1.1.0\n"
        )

        expected = (
            "  AOTOOLS_VERSION: 1.0.9\n"
            "  SEQFILE_VERSION: 0.2.0\n"
            "  EIDOS_VERSION: 1.1.0\n"
        )

        updated = replace_version_variable(
            original,
            "AOTOOLS_VERSION",
            "1.0.8",
            "1.0.9",
        )

        self.assertEqual(updated, expected)
        self.assertEqual(original.count("1.0.8"), 1)

    def test_duplicate_version_declarations_are_rejected(self):
        """Reject multiple matching version declarations."""

        original = (
            "  AOTOOLS_VERSION: 1.0.8\n"
            "  SEQFILE_VERSION: 0.2.0\n"
            "  AOTOOLS_VERSION: 1.0.8\n"
        )

        with self.assertRaisesRegex(
            ScanError,
            "Expected exactly one AOTOOLS_VERSION declaration",
        ):
            replace_version_variable(
                original,
                "AOTOOLS_VERSION",
                "1.0.8",
                "1.0.9",
            )

    def test_incorrect_current_version_is_rejected(self):
        """Reject changes when the configured version differs."""

        original = (
            "  AOTOOLS_VERSION: 1.0.9\n"
            "  SEQFILE_VERSION: 0.2.0\n"
        )

        with self.assertRaisesRegex(
            ScanError,
            "Expected exactly one AOTOOLS_VERSION declaration",
        ):
            replace_version_variable(
                original,
                "AOTOOLS_VERSION",
                "1.0.8",
                "1.0.10",
            )
    def test_preserves_blank_lines_and_other_versions(self):
        """Only update AOtools, preserving surrounding formatting."""
        original = (
            "  IDG_VERSION: 1.2.0\n"
            "\n"
            "  AOTOOLS_VERSION: 1.0.8\n"
            "\n"
            "  TOOLS_21CM_VERSION: 2.4.3\n"
        )

        expected = (
            "  IDG_VERSION: 1.2.0\n"
            "\n"
            "  AOTOOLS_VERSION: 1.0.9\n"
            "\n"
            "  TOOLS_21CM_VERSION: 2.4.3\n"
        )

        result = replace_version_variable(
            original,
            "AOTOOLS_VERSION",
            "1.0.8",
            "1.0.9",
        )

        self.assertEqual(result, expected)
class TestDependencyVersionUpdates(unittest.TestCase):
    def test_single_dependency_version(self):
        content = (
            "env:\n"
            "  IDG_VERSION: 1.2.0\n"
            "  EVERYBEAM_VERSION: 0.8.0\n"
        )

        updated = prepare_dependency_version_updates(
            content,
            {"IDG_VERSION": "1.2.0"},
            "1.3.0",
        )

        self.assertEqual(
            updated,
            (
                "env:\n"
                "  IDG_VERSION: 1.3.0\n"
                "  EVERYBEAM_VERSION: 0.8.0\n"
            ),
        )

    def test_coupled_dependency_versions(self):
        content = (
            "env:\n"
            "  OSKAR_VERSION: 2.12.2\n"
            "  OSKARPY_VERSION: 2.12.2  # keep synchronized\n"
        )

        updated = prepare_dependency_version_updates(
            content,
            {
                "OSKAR_VERSION": "2.12.2",
                "OSKARPY_VERSION": "2.12.2",
            },
            "2.13.0",
        )

        self.assertIn("OSKAR_VERSION: 2.13.0\n", updated)
        self.assertIn(
            "OSKARPY_VERSION: 2.13.0  # keep synchronized",
            updated,
        )

    def test_coupled_dependency_rejects_mismatched_versions(self):
        content = (
            "env:\n"
            "  OSKAR_VERSION: 2.12.2\n"
            "  OSKARPY_VERSION: 2.12.1\n"
        )

        with self.assertRaises(ScanError):
            prepare_dependency_version_updates(
                content,
                {
                    "OSKAR_VERSION": "2.12.2",
                    "OSKARPY_VERSION": "2.12.1",
                },
                "2.13.0",
            )

    def test_missing_variable_rejects_entire_update(self):
        content = "env:\n  OSKAR_VERSION: 2.12.2\n"

        with self.assertRaises(ScanError):
            prepare_dependency_version_updates(
                content,
                {
                    "OSKAR_VERSION": "2.12.2",
                    "OSKARPY_VERSION": "2.12.2",
                },
                "2.13.0",
            )

class TestRecipeChecksumReplacement(unittest.TestCase):

    def test_replace_aotools_sha256(self):
        """Update the checksum and version comment only."""

        old_sha256 = "a" * 64
        new_sha256 = "b" * 64

        original = (
            "source:\n"
            "  url: example.tar.gz\n"
            f"  sha256: {old_sha256}  # 1.0.8 sdist\n"
            "\n"
            "build:\n"
            "  number: {{ build }}\n"
        )

        expected = (
            "source:\n"
            "  url: example.tar.gz\n"
            f"  sha256: {new_sha256}  # 1.0.9 sdist\n"
            "\n"
            "build:\n"
            "  number: {{ build }}\n"
        )

        updated = replace_recipe_sha256(
            original,
            "1.0.8",
            "1.0.9",
            new_sha256,
        )

        self.assertEqual(updated, expected)
    def test_invalid_target_sha256_is_rejected(self):
        """Reject an invalid checksum before modifying recipe content."""
        original = ("source:\n"
            f"  sha256: {'a' * 64}  # 1.0.8 sdist\n"
        )

        with self.assertRaisesRegex(
            ScanError,
            "Invalid target SHA256 checksum",
        ):
            replace_recipe_sha256(
                original,
                "1.0.8",
                "1.0.9",
                "invalid-checksum",
            )

    def test_mismatched_version_comment_is_rejected(self):
        """Reject a checksum whose version comment is outdated."""

        original = (
            "source:\n"
            f"  sha256: {'a' * 64}  # 1.0.7 sdist\n"
        )

        with self.assertRaisesRegex(
            ScanError,
            "Expected exactly one SHA256 declaration",
        ):
            replace_recipe_sha256(
                original,
                "1.0.8",
                "1.0.9",
                "b" * 64,
            )

class TestReleaseMetadataUpdate(unittest.TestCase):

    def test_update_version_and_reset_build_number(self):
        original = (
            '{\n'
            '  "version": "1.0.8",\n'
            '  "build_number": 1,\n'
            '  "channel": "main"\n'
            '}\n'
        )

        updated = update_release_metadata(original, "1.0.9")
        data = json.loads(updated)

        self.assertEqual(data["version"], "1.0.9")
        self.assertEqual(data["build_number"], 0)
        self.assertEqual(data["channel"], "main")

    def test_invalid_build_numbers_are_rejected(self):
        for build_number in [-1, "1", True, None]:
            config = json.dumps({
                "version": "1.0.8",
                "build_number": build_number,
            })

            with self.subTest(build_number=build_number):
                with self.assertRaisesRegex(
                    ScanError,
                    "Invalid build_number in release.json",
                ):
                    update_release_metadata(config, "1.0.9")

    def test_missing_version_is_rejected(self):
        config = '{"build_number": 1}'

        with self.assertRaisesRegex(
            ScanError,
            "Missing version in release.json",
        ):
            update_release_metadata(config, "1.0.9")

    def test_malformed_json_is_rejected(self):
        with self.assertRaisesRegex(
            ScanError,
            "Invalid release.json content",
        ):
            update_release_metadata('{"build_number": 1', "1.0.9")


class TestExternalReleaseMetadata(unittest.TestCase):

    def test_update_fftw_version_without_build_number(self):
        original = '{"version": "3.3.10"}'

        updated = update_release_metadata(
            original,
            "3.3.11",
            require_build_number=False,
        )

        self.assertEqual(
            json.loads(updated),
            {"version": "3.3.11"},
        )
        self.assertTrue(updated.endswith("\n"))

    def test_preserve_additional_metadata(self):
        original = json.dumps({
            "version": "3.3.10",
            "channel": "conda-forge",
        })

        updated = update_release_metadata(
            original,
            "3.3.11",
            require_build_number=False,
        )

        self.assertEqual(json.loads(updated), {
            "version": "3.3.11",
            "channel": "conda-forge",
        })

    def test_external_build_number_is_rejected(self):
        original = '{"version": "3.3.10", "build_number": 0}'

        with self.assertRaisesRegex(
            ScanError,
            "Unexpected build_number",
        ):
            update_release_metadata(
                original,
                "3.3.11",
                require_build_number=False,
            )

    def test_external_missing_version_is_rejected(self):
        with self.assertRaisesRegex(ScanError, "Missing version"):
            update_release_metadata(
                '{}',
                "3.3.11",
                require_build_number=False,
            )

    def test_internal_missing_build_number_is_rejected(self):
        with self.assertRaisesRegex(ScanError, "Invalid build_number"):
            update_release_metadata(
                '{"version": "2.2.0"}',
                "2.5.1",
            )


class TestDryRunDiff(unittest.TestCase):

    def test_show_diff(self):
        """Display a unified diff without modifying content."""

        original = "  AOTOOLS_VERSION: 1.0.8\n"
        updated = "  AOTOOLS_VERSION: 1.0.9\n"

        output = io.StringIO()

        with redirect_stdout(output):
            show_diff(
                Path(".github/workflows/build_base.yml"),
                original,
                updated,
            )

        diff = output.getvalue()

        self.assertIn("--- a/.github/workflows/build_base.yml", diff)
        self.assertIn("+++ b/.github/workflows/build_base.yml", diff)
        self.assertIn("-  AOTOOLS_VERSION: 1.0.8", diff)
        self.assertIn("+  AOTOOLS_VERSION: 1.0.9", diff)
        self.assertEqual(original, "  AOTOOLS_VERSION: 1.0.8\n")


class TestDryRunIntegration(unittest.TestCase):

    def test_two_file_dry_run(self):
        """Preview AOtools recipe and release changes without writing files."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            recipe_path = root / "aotools/meta.yaml"
            release_path = root / "aotools/release.json"

            recipe_path.parent.mkdir(parents=True)

            originals = {
                recipe_path: (
                    "source:\n"
                    f"  sha256: {'a' * 64}  # 1.0.8 sdist\n"
                ),
                release_path: (
                    '{\n'
                    '  "version": "1.0.8",\n'
                    '  "build_number": 1\n'
                    '}\n'
                ),
            }

            for path, content in originals.items():
                path.write_text(content, encoding="utf-8")

            output = io.StringIO()

            with redirect_stdout(output):
                show_diff(
                    recipe_path.relative_to(root),
                    originals[recipe_path],
                    replace_recipe_sha256(
                        originals[recipe_path],
                        "1.0.8",
                        "1.0.9",
                        "b" * 64,
                    ),
                )

                show_diff(
                    release_path.relative_to(root),
                    originals[release_path],
                    update_release_metadata(
                        originals[release_path],
                        "1.0.9",
                    ),
                )

            diff = output.getvalue()

            self.assertIn(f"+  sha256: {'b' * 64}", diff)
            self.assertIn('+"version":"1.0.9"', diff.replace(" ", ""))
            self.assertIn('+"build_number":0', diff.replace(" ", ""))

            self.assertNotIn("build_base.yml", diff)

            for path, original in originals.items():
                self.assertEqual(
                    path.read_text(encoding="utf-8"),
                    original,
                )


class TestMainDryRun(unittest.TestCase):
    """Test the complete AOtools planner."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.root = Path(self.temp_dir.name)

        self.build_path = (
            self.root / ".github/workflows/build_base.yml"
        )
        self.recipe_path = self.root / "aotools/meta.yaml"
        self.release_path = self.root / "aotools/release.json"

        self.build_path.parent.mkdir(parents=True)
        self.recipe_path.parent.mkdir(parents=True)

        self.originals = {
            self.build_path: "  AOTOOLS_VERSION: 1.0.8\n",
            self.recipe_path: (
                "source:\n"
                f"  sha256: {'a' * 64}  # 1.0.8 sdist\n"
            ),
            self.release_path: (
                '{\n'
                '  "version": "1.0.8",\n'
                '  "build_number": 1\n'
                '}\n'
            ),
        }

        for path, content in self.originals.items():
            path.write_text(content, encoding="utf-8")

        self.registry = Mock()
        self.registry.settings.build_base = (
            ".github/workflows/build_base.yml"
        )
        self.registry.settings.http_timeout_seconds = 20

        dependency = Mock()
        dependency.id = "aotools"
        dependency.name = "AOtools"
        dependency.package = "aotools"
        dependency.version_variables = ["AOTOOLS_VERSION"]
        dependency.recipes = ["aotools/meta.yaml"]
        dependency.version_file = "aotools/release.json"

        self.registry.dependencies = [dependency]

    def run_planner(self, write=False):
        """Run main with mocked registry and PyPI metadata."""

        arguments = [
            "--dependency",
            "aotools",
            "--version",
            "1.0.9",
        ]

        if write:
            arguments.append("--write")

        output = io.StringIO()

        with (
            patch(
                "prepare_dependency_upgrade.load_registry",
                return_value=self.registry,
            ),
            patch(
                "prepare_dependency_upgrade.validate_registry"
            ),
            patch(
                "prepare_dependency_upgrade.get_pypi_sdist",
                return_value=(
                    "aotools-1.0.9.tar.gz",
                    "b" * 64,
                ),
            ),
            redirect_stdout(output),
        ):
            main(arguments, root=self.root)

        return output.getvalue()

    def test_main_previews_two_files_without_writing(self):
        """Dry-run previews two changes without modifying files."""

        preview = self.run_planner(write=False)

        self.assertNotIn("build_base.yml", preview)
        self.assertIn(f"+  sha256: {'b' * 64}", preview)
        self.assertIn('+  "version": "1.0.9"', preview)
        self.assertIn('+  "build_number": 0', preview)
        self.assertIn("Mode: read-only planning", preview)
        self.assertIn("No files were modified.", preview)

        for path, original in self.originals.items():
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                original,
            )

    def test_main_writes_two_files(self):
        """Write mode updates only recipe and release metadata."""

        preview = self.run_planner(write=True)

        self.assertIn("Mode: write", preview)
        self.assertIn(
            "All prepared changes were applied.",
            preview,
        )

        # Workflow must remain unchanged.
        self.assertEqual(
            self.build_path.read_text(encoding="utf-8"),
            self.originals[self.build_path],
        )

        # Recipe checksum must be updated.
        self.assertIn(
            f"sha256: {'b' * 64}  # 1.0.9 sdist",
            self.recipe_path.read_text(encoding="utf-8"),
        )

        # Release metadata must contain the new version and build number.
        release_data = json.loads(
            self.release_path.read_text(encoding="utf-8")
        )

        self.assertEqual(release_data["version"], "1.0.9")
        self.assertEqual(release_data["build_number"], 0)


class TestApplyChanges(unittest.TestCase):
    """Verify controlled updates using temporary files."""

    def test_applies_all_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "build_base.yml"
            second = root / "meta.yaml"

            first.write_text("version: 1.0.8\n", encoding="utf-8")
            second.write_text("sha256: old\n", encoding="utf-8")

            changes = {
                first: ("version: 1.0.8\n", "version: 1.0.9\n"),
                second: ("sha256: old\n", "sha256: new\n"),
            }

            apply_changes(changes)

            self.assertEqual(
                first.read_text(encoding="utf-8"),
                "version: 1.0.9\n",
            )
            self.assertEqual(
                second.read_text(encoding="utf-8"),
                "sha256: new\n",
            )

    def test_rejects_modified_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meta.yaml"
            path.write_text("unexpected\n", encoding="utf-8")

            changes = {
                path: ("expected\n", "updated\n"),
            }

            with self.assertRaises(ScanError):
                apply_changes(changes)

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "unexpected\n",
            )

    def test_rolls_back_after_replace_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.yml"
            second = root / "second.yml"

            first.write_text("old-first\n", encoding="utf-8")
            second.write_text("old-second\n", encoding="utf-8")

            changes = {
                first: ("old-first\n", "new-first\n"),
                second: ("old-second\n", "new-second\n"),
            }

            original_replace = os.replace
            calls = 0

            def fail_second_replace(src, dst):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("Simulated replacement failure")
                return original_replace(src, dst)

            with patch(
                "prepare_dependency_upgrade.os.replace",
                side_effect=fail_second_replace,
            ):
                with self.assertRaises(OSError):
                    apply_changes(changes)

            self.assertEqual(
                first.read_text(encoding="utf-8"),
                "old-first\n",
            )
            self.assertEqual(
                second.read_text(encoding="utf-8"),
                "old-second\n",
            )





class TestIDGMainIntegration(unittest.TestCase):
    """Test IDG through the complete CLI preparation entry point."""

    def setUp(self):
        from types import SimpleNamespace

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

        self.build_path = (
            self.root / ".github/workflows/build_base.yml"
        )
        self.build_path.parent.mkdir(parents=True)

        self.original = (
            "env:\n"
            "  IDG_VERSION: 1.2.0\n"
            "  EVERYBEAM_VERSION: 0.8.0\n"
        )
        self.build_path.write_text(
            self.original, encoding="utf-8"
        )
        self.release_path = self.root / "idg/release.json"
        self.release_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.original_release = (
            '{\n'
            '  "version": "1.2.0",\n'
            '  "build_number": 0\n'
            '}\n'
        )

        self.release_path.write_text(
            self.original_release,
            encoding="utf-8",
        )

        recipe = self.root / "idg/meta.yaml"
        recipe.parent.mkdir(parents=True, exist_ok=True)
        recipe.write_text(
            "source:\n"
            "  git_url: https://gitlab.com/astron-idg/idg.git\n"
            "  git_tag: {{ IDG_VERSION }}\n",
            encoding="utf-8",
        )

        registry_path = self.root / ".github/dependency-scan.toml"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text("", encoding="utf-8")

        self.idg = SimpleNamespace(
            id="idg",
            name="IDG",
            source_type="gitlab-tags",
            version_variables=("IDG_VERSION",),
            version_file="idg/release.json",
            recipes=("idg/meta.yaml",),
        )

        self.unsupported = SimpleNamespace(
            id="unimplemented",
            name="Unimplemented",
            source_type="gitlab-tags",
            version_variables=("EVERYBEAM_VERSION",),
            recipes=("everybeam/meta.yaml",),
        )

        self.registry = SimpleNamespace(
            dependencies=[self.idg, self.unsupported],
            settings=SimpleNamespace(
                build_base=".github/workflows/build_base.yml",
                http_timeout_seconds=20,
            ),
        )

        self.versions = {
            "IDG_VERSION": "1.2.0",
            "EVERYBEAM_VERSION": "0.8.0",
        }

    def run_main(self, args):
        from prepare_dependency_upgrade import main

        output = io.StringIO()

        with (
            patch(
                "prepare_dependency_upgrade.load_registry",
                return_value=self.registry,
            ),
            patch(
                "prepare_dependency_upgrade.load_declared_versions",
                return_value=self.versions,
            ),
            patch("prepare_dependency_upgrade.validate_registry"),
            patch("prepare_dependency_upgrade.verify_idg_tag") as verify,
            redirect_stdout(output),
        ):
            main(args, root=self.root)

        return output.getvalue(), verify


    def test_idg_main_dry_run_does_not_write(self):
        output, verify = self.run_main(
            ["--dependency", "idg", "--version", "1.3.0"]
        )

        self.assertIn("--- a/idg/release.json", output)
        self.assertIn("+++ b/idg/release.json", output)
        self.assertIn('-  "version": "1.2.0"', output)
        self.assertIn('+  "version": "1.3.0"', output)
        self.assertIn("Mode: read-only planning", output)
        self.assertNotIn("build_base.yml", output)

        # Dry-run must not change either file.
        self.assertEqual(
            self.release_path.read_text(encoding="utf-8"),
            self.original_release,
        )
        self.assertEqual(
            self.build_path.read_text(encoding="utf-8"),
            self.original,
        )

        verify.assert_called_once_with("1.3.0", timeout=20)

    def test_unsupported_dependency_is_rejected(self):
        from prepare_dependency_upgrade import main
        from dependency_scan import ScanError

        with (
            patch(
                "prepare_dependency_upgrade.load_registry",
                return_value=self.registry,
            ),
            patch(
                "prepare_dependency_upgrade.load_declared_versions",
                return_value=self.versions,
            ),
            patch("prepare_dependency_upgrade.validate_registry"),
            patch("prepare_dependency_upgrade.verify_idg_tag") as verify,
        ):
            with self.assertRaisesRegex(
                ScanError,
                "No safe upgrade preparation strategy",
            ):
                main(
                    [
                        "--dependency", "unimplemented",
                        "--version", "0.9.1",
                    ],
                    root=self.root,
                )

        verify.assert_not_called()
        self.assertEqual(
            self.build_path.read_text(encoding="utf-8"),
            self.original,
        )


class TestIDGUpgradePreparation(unittest.TestCase):
    """IDG upgrades must be verified, targeted and read-only."""

    def setUp(self):
        from types import SimpleNamespace

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

        # Shared build configuration must remain unchanged.
        self.build_path = (
            self.root / ".github/workflows/build_base.yml"
        )
        self.build_path.parent.mkdir(parents=True)

        self.original = (
            "env:\n"
            "  IDG_VERSION: 1.2.0\n"
            "  EVERYBEAM_VERSION: 0.8.0\n"
        )
        self.build_path.write_text(
            self.original,
            encoding="utf-8",
        )

        # IDG version is now stored in release.json.
        self.release_path = self.root / "idg/release.json"
        self.release_path.parent.mkdir(parents=True, exist_ok=True)

        self.original_release = (
            '{\n'
            '  "version": "1.2.0",\n'
            '  "build_number": 0\n'
            '}\n'
        )

        self.release_path.write_text(
            self.original_release,
            encoding="utf-8",
        )
        # Preserve the existing IDG recipe.
        recipe_path = self.root / "idg/meta.yaml"
        recipe_path.write_text(
            "source:\n"
            "  git_url: https://gitlab.com/astron-idg/idg.git\n"
            "  git_tag: {{ IDG_VERSION }}\n",
            encoding="utf-8",
        )

        self.registry = SimpleNamespace(
            settings=SimpleNamespace(
                build_base=".github/workflows/build_base.yml",
                http_timeout_seconds=20,
            )
        )
        self.dependency = SimpleNamespace(
            id="idg",
            source_type="gitlab-tags",
            version_variables=("IDG_VERSION",),
            version_file="idg/release.json",
            recipes=("idg/meta.yaml",),
        )

    @patch("prepare_dependency_upgrade.verify_idg_tag")
    def test_prepares_only_release_metadata(self, verify):
        from prepare_dependency_upgrade import prepare_idg_changes

        changes = prepare_idg_changes(
            self.root,
            self.registry,
            self.dependency,
            {"IDG_VERSION": "1.2.0"},
            "1.3.0",
        )

        # Only release.json may be changed.
        self.assertEqual(list(changes), [self.release_path])

        original, updated = changes[self.release_path]

        self.assertEqual(original, self.original_release)

        import json

        updated_data = json.loads(updated)

        self.assertEqual(updated_data["version"], "1.3.0")
        self.assertEqual(updated_data["build_number"], 0)

        # Preparation must not write files.
        self.assertEqual(
            self.release_path.read_text(encoding="utf-8"),
            self.original_release,
        )
        self.assertEqual(
            self.build_path.read_text(encoding="utf-8"),
            self.original,
        )
        verify.assert_called_once_with("1.3.0", timeout=20)

    @patch("prepare_dependency_upgrade.verify_idg_tag")
    def test_rejects_missing_release_metadata(self, verify):
        from prepare_dependency_upgrade import prepare_idg_changes

        self.release_path.unlink()

        with self.assertRaises((ScanError, FileNotFoundError)):
            prepare_idg_changes(
                self.root,
                self.registry,
                self.dependency,
                {"IDG_VERSION": "1.2.0"},
                "1.3.0",
            )

    @patch("prepare_dependency_upgrade.verify_idg_tag")
    def test_rejects_incorrect_version_file(self, verify):
        from prepare_dependency_upgrade import prepare_idg_changes

        self.dependency.version_file = (
            ".github/workflows/build_base.yml"
        )

        with self.assertRaises(ScanError):
            prepare_idg_changes(
                self.root,
                self.registry,
                self.dependency,
                {"IDG_VERSION": "1.2.0"},
                "1.3.0",
            )

        verify.assert_not_called()

    @patch("prepare_dependency_upgrade.verify_idg_tag")
    def test_rejects_incompatible_recipe(self, verify):
        from prepare_dependency_upgrade import prepare_idg_changes

        (self.root / "idg/meta.yaml").write_text(
            "source:\n"
            "  git_tag: main\n",
            encoding="utf-8",
        )

        with self.assertRaises(ScanError):
            prepare_idg_changes(
                self.root,
                self.registry,
                self.dependency,
                {"IDG_VERSION": "1.2.0"},
                "1.3.0",
            )
        verify.assert_not_called()

    @patch("prepare_dependency_upgrade.verify_idg_tag")
    def test_rejects_unexpected_git_repository(self, verify):
        from prepare_dependency_upgrade import prepare_idg_changes

        (self.root / "idg/meta.yaml").write_text(
            "source:\n"
            "  git_url: https://example.com/other/idg.git\n"
            "  git_tag: {{ IDG_VERSION }}\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ScanError,
            "unexpected Git repository",
        ):
            prepare_idg_changes(
                self.root,
                self.registry,
                self.dependency,
                {"IDG_VERSION": "1.2.0"},
                "1.3.0",
            )

        verify.assert_not_called()

    @patch("subprocess.run")
    def test_verifies_exact_upstream_tag(self, run):
        from prepare_dependency_upgrade import verify_idg_tag

        run.return_value.returncode = 0
        run.return_value.stdout = (
            "a" * 40 + "\trefs/tags/1.3.0\n"
        )

        verify_idg_tag("1.3.0")
        command = run.call_args.args[0]
        self.assertEqual(
            command[-1],
            "refs/tags/1.3.0",
        )

    @patch("subprocess.run")
    def test_rejects_missing_upstream_tag(self, run):
        from prepare_dependency_upgrade import verify_idg_tag

        run.return_value.returncode = 2
        run.return_value.stdout = ""

        with self.assertRaises(ScanError):
            verify_idg_tag("1.3.0")




class TestFFTWPreparation(unittest.TestCase):
    def setUp(self):
        import sys
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        self.patch = patch
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        self.addCleanup(sys.path.pop, 0)
        import prepare_dependency_upgrade as planner
        self.planner = planner
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / '.github/workflows/build_base.yml'
        self.path.parent.mkdir(parents=True)
        self.original = 'env:\n  FFTW3_VERSION: 3.3.10\n  FFTW3F_VERSION: 3.3.10\n  FINUFFT_VERSION: 2.2.0\n'
        self.path.write_text(self.original, encoding='utf-8')
        self.release = self.root / "fftw/release.json"
        self.release.parent.mkdir()
        self.release_original = '{\n  "version": "3.3.10"\n}\n'
        self.release_updated = '{\n  "version": "3.3.11"\n}\n'
        self.release.write_text(self.release_original, encoding="utf-8")
        self.recipe = self.root / 'finufft/meta.yaml'
        self.recipe.parent.mkdir()
        self.pins = 'build:\n  - fftw =={{ FFTW3_VERSION_ALT }}\nhost:\n  - fftw =={{ FFTW3_VERSION_ALT }}\nrun:\n  - fftw =={{ FFTW3_VERSION_ALT }}\n'
        self.recipe.write_text(self.pins, encoding='utf-8')
        self.dependency = SimpleNamespace(id='fftw', name='FFTW', source_type='github-tags', repository='FFTW/fftw3', version_variables=('FFTW3_VERSION',), recipes=('finufft/meta.yaml',), version_file='fftw/release.json')
        self.registry = SimpleNamespace(settings=SimpleNamespace(http_timeout_seconds=20), dependencies=[self.dependency])
        self.current = {'FFTW3_VERSION': '3.3.10'}

    def prepare(self):
        with self.patch.object(self.planner, 'verify_fftw_tag') as verify:
            changes = self.planner.prepare_fftw_changes(self.root, self.registry, self.dependency, self.current, '3.3.11')
            verify.assert_called_once_with('3.3.11', 20)
            return changes

    def test_prepares_only_fftw_version(self):
        changes = self.prepare()
        self.assertEqual(
            changes,
            {self.release: (self.release_original, self.release_updated)},
        )
        self.planner.validate_upgrade_paths(self.root, changes)
        self.assertEqual(self.release.read_text(), self.release_original)
        self.assertEqual(self.recipe.read_text(), self.pins)
        self.assertEqual(self.path.read_text(), self.original)

    def test_rejects_invalid_registry(self):
        self.dependency.repository = 'unexpected/repo'
        with self.assertRaises(self.planner.ScanError):
            self.prepare()

    def test_rejects_missing_or_duplicate_pins(self):
        for content in ('', self.pins + '  - fftw =={{ FFTW3_VERSION_ALT }}\n'):
            with self.subTest(content=content):
                self.recipe.write_text(content)
                with self.assertRaises(self.planner.ScanError):
                    self.prepare()

    def test_valid_tag(self):
        from subprocess import CompletedProcess
        ref = 'refs/tags/fftw-3.3.11'
        with self.patch('subprocess.run', return_value=CompletedProcess([], 0, 'a'*40 + '\t' + ref + '\n', '')) as run:
            self.planner.verify_fftw_tag('3.3.11')
            self.assertEqual(run.call_args.args[0][-1], ref)

    def test_missing_or_invalid_tag(self):
        from subprocess import CompletedProcess
        for result in (CompletedProcess([], 2, '', ''), CompletedProcess([], 0, 'invalid\n', ''), CompletedProcess([], 0, 'a'*40 + '\trefs/tags/wrong\n', '')):
            with self.subTest(result=result), self.patch('subprocess.run', return_value=result):
                with self.assertRaises(self.planner.ScanError):
                    self.planner.verify_fftw_tag('3.3.11')

    def run_main(self, version='3.3.11', write=False):
        from contextlib import ExitStack, redirect_stdout
        from io import StringIO
        with ExitStack() as stack:
            for name, value in [('load_registry', self.registry), ('load_declared_versions', self.current), ('validate_registry', None), ('verify_fftw_tag', None)]:
                stack.enter_context(self.patch.object(self.planner, name, return_value=value))
            stack.enter_context(redirect_stdout(StringIO()))
            args = ['--dependency', 'fftw', '--version', version]
            if write:
                args.append('--write')
            self.planner.main(args, root=self.root)

    def test_main_read_only(self):
        self.run_main()
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(self.recipe.read_text(), self.pins)
        self.assertEqual(self.release.read_text(), self.release_original)

    def test_main_write(self):
        self.run_main(write=True)
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(self.release.read_text(), self.release_updated)
        self.assertEqual(self.recipe.read_text(), self.pins)

    def test_rejects_invalid_metadata_path(self):
        for metadata in ("", ".github/workflows/build_base.yml"):
            with self.subTest(metadata=metadata):
                self.dependency.version_file = metadata
                with self.assertRaises(self.planner.ScanError):
                    self.prepare()

    def test_rejects_external_build_number(self):
        self.release.write_text(
            '{"version": "3.3.10", "build_number": 0}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            self.planner.ScanError, "Unexpected build_number"
        ):
            self.prepare()

    def test_main_rejects_workflow_modification(self):
        changes = {self.path: (self.original, "forbidden")}

        with self.patch.object(
            self.planner, "prepare_fftw_changes", return_value=changes
        ):
            for write in (False, True):
                with self.subTest(write=write):
                    with self.assertRaisesRegex(
                        self.planner.ScanError,
                        "must not modify workflow files",
                    ):
                        self.run_main(write=write)

        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(self.release.read_text(), self.release_original)
        self.assertEqual(self.recipe.read_text(), self.pins)

    def test_main_rejects_equal_or_older_version(self):
        for version in ('3.3.10', '3.3.9'):
            with self.subTest(version=version), self.assertRaises(self.planner.ScanError):
                self.run_main(version, write=True)
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(self.release.read_text(), self.release_original)
        self.assertEqual(self.recipe.read_text(), self.pins)



class TestFINUFFTPreparation(unittest.TestCase):

    def setUp(self):
        import prepare_dependency_upgrade as planner
        from types import SimpleNamespace

        self.planner = planner
        self.version = "2.5.1"
        self.http = Mock()

        self.filename = (
            "finufft-2.5.1-py3-none-"
            "manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
        )

        self.wheel = {
            "filename": self.filename,
            "packagetype": "bdist_wheel",
            "yanked": False,
            "url": (
                "https://files.pythonhosted.org/packages/ab/cd/"
                + self.filename
            ),
            "digests": {"sha256": "a" * 64},
            "requires_python": ">=3.8",
        }

        import base64

        self.native_response = {
            "encoding": "base64",
            "content": base64.b64encode(
                b"project(FINUFFT VERSION 2.5.1 LANGUAGES C CXX)"
            ).decode(),
        }

        self.http.get_json.return_value = {"urls": [self.wheel]}

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

        self.release = self.root / "finufft/release.json"
        self.release.parent.mkdir()

        self.original = json.dumps({
            "version": "2.2.0",
            "build_number": 2,
            "wheel": {"filename": "old.whl"},
        })

        self.release.write_text(self.original)

        self.dependency = SimpleNamespace(
            id="finufft",
            source_type="pypi",
            package="finufft",
            version_variables=("FINUFFT_VERSION",),
            recipes=("finufft/meta.yaml",),
            version_file="finufft/release.json",
        )

        self.registry = SimpleNamespace(
            settings=SimpleNamespace(http_timeout_seconds=20)
        )

    def select_wheel(self):
        return self.planner.get_finufft_wheel(
            self.version, self.http
        )

    def test_selects_py3_wheel(self):
        result = self.select_wheel()
        self.assertEqual(result["filename"], self.filename)
        self.assertEqual(result["url"], self.wheel["url"])
        self.assertEqual(result["sha256"], "a" * 64)

    def test_selects_cp312_wheel(self):
        filename = self.filename.replace(
            "py3-none", "cp312-cp312"
        )
        self.wheel["filename"] = filename
        self.wheel["url"] = (
            "https://files.pythonhosted.org/packages/ab/cd/"
            + filename
        )
        self.assertEqual(
            self.select_wheel()["filename"], filename
        )

    def test_rejects_yanked_wheel(self):
        self.wheel["yanked"] = True
        with self.assertRaises(ScanError):
            self.select_wheel()

    def test_rejects_missing_wheel(self):
        self.http.get_json.return_value = {"urls": []}
        with self.assertRaises(ScanError):
            self.select_wheel()

    def test_rejects_incompatible_wheel(self):
        self.wheel["filename"] = self.filename.replace(
            "x86_64", "aarch64"
        )
        with self.assertRaises(ScanError):
            self.select_wheel()

    def test_rejects_invalid_checksum(self):
        self.wheel["digests"]["sha256"] = "invalid"
        with self.assertRaises(ScanError):
            self.select_wheel()

    def test_rejects_invalid_url(self):
        self.wheel["url"] = "https://example.com/" + self.filename
        with self.assertRaises(ScanError):
            self.select_wheel()

    def test_rejects_ambiguous_wheels(self):
        self.http.get_json.return_value = {
            "urls": [self.wheel, self.wheel.copy()]
        }
        with self.assertRaises(ScanError):
            self.select_wheel()

    def test_accepts_matching_native_version(self):
        self.http.get_json.return_value = self.native_response
        self.planner.verify_finufft_native_version(
            self.version, self.http
        )

    def test_rejects_native_version_mismatch(self):
        import base64

        self.native_response["content"] = base64.b64encode(
            b"project(FINUFFT VERSION 2.5.0 LANGUAGES C CXX)"
        ).decode()

        self.http.get_json.return_value = self.native_response

        with self.assertRaisesRegex(ScanError, "native version mismatch"):
            self.planner.verify_finufft_native_version(
                self.version, self.http
            )

    def test_rejects_invalid_native_response(self):
        self.http.get_json.return_value = {}

        with self.assertRaises(ScanError):
            self.planner.verify_finufft_native_version(
                self.version, self.http
            )

    def test_rejects_incompatible_python(self):
        self.wheel["requires_python"] = ">=3.13"

        with self.assertRaises(ScanError):
            self.select_wheel()

    def test_rejects_invalid_python_requirement(self):
        self.wheel["requires_python"] = "invalid"

        with self.assertRaises(ScanError):
            self.select_wheel()

    def test_rejects_missing_python_requirement(self):
        del self.wheel["requires_python"]

        with self.assertRaises(ScanError):
            self.select_wheel()

    def test_prepares_only_release_metadata(self):
        self.http.get_json.side_effect = [
            self.native_response,
            {"urls": [self.wheel]},
        ]
        with patch.object(
            self.planner, "HttpClient", return_value=self.http
        ):
            changes = self.planner.prepare_finufft_changes(
                self.root,
                self.registry,
                self.dependency,
                {"FINUFFT_VERSION": "2.2.0"},
                self.version,
            )

        self.assertEqual(set(changes), {self.release})

        original, updated = changes[self.release]
        data = json.loads(updated)

        self.assertEqual(original, self.original)
        self.assertEqual(data["version"], "2.5.1")
        self.assertEqual(data["build_number"], 0)
        self.http.get_json.side_effect = None
        self.assertEqual(data["wheel"], self.select_wheel())

        self.planner.validate_upgrade_paths(self.root, changes)
        self.assertEqual(self.release.read_text(), self.original)


if __name__ == "__main__":
    unittest.main()
