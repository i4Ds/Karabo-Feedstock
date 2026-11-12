
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

if __name__ == "__main__":
    unittest.main()
