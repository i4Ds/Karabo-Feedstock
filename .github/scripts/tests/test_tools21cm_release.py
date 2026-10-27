import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PATH = Path(__file__).resolve().parents[1] / "tools21cm_release.py"
spec = importlib.util.spec_from_file_location("tools21cm_release", PATH)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def artifact(platform, variant, version, build, labels=None):
    """Create a simulated Anaconda package record for testing."""
    return {
        "basename": f"{platform}/tools21cm-file.conda",
        "version": version,
        "attrs": {
            "build_number": build,
            "build": f"{variant}h123_{build}",
        },
        "labels": ["main"] if labels is None else labels,
    }


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        # Read the current release settings from the repository.
        self.version, self.build, self.variants = release.release_identity()

        # A release contains one package for each platform and Python variant.
        self.expected_packages = [
            artifact(platform, variant, self.version, self.build)
            for platform in sorted(release.PLATFORMS)
            for variant in sorted(self.variants)
        ]

    def test_missing_and_complete(self):
        self.assertEqual(
            release.publication_state(
                [], self.version, self.build, self.variants
            ),
            "missing",
        )

        self.assertEqual(
            release.publication_state(
                self.expected_packages,
                self.version,
                self.build,
                self.variants,
            ),
            "complete",
        )

    def test_partial_fails(self):
        # Simulate a release where only one expected package exists.
        with self.assertRaisesRegex(ValueError, "Partial"):
            release.publication_state(
                self.expected_packages[:1],
                self.version,
                self.build,
                self.variants,
            )

    def test_unrelated_files_do_not_count(self):
        variant = sorted(self.variants)[0]
        platform = sorted(release.PLATFORMS)[0]

        unrelated_files = [
            artifact(platform, variant, self.version + ".999", self.build),
            artifact(platform, variant, self.version, self.build + 1),
            artifact(
                platform,
                variant,
                self.version,
                self.build,
                labels=["dev"],
            ),
            artifact("other-platform", variant, self.version, self.build),
            artifact(platform, "py999999", self.version, self.build),
        ]

        self.assertEqual(
            release.publication_state(
                unrelated_files,
                self.version,
                self.build,
                self.variants,
            ),
            "missing",
        )

    def test_repository_identity(self):
        self.assertIsInstance(self.version, str)
        self.assertIsInstance(self.build, int)
        self.assertGreaterEqual(self.build, 0)
        self.assertTrue(self.variants)

    def test_invalid_build_and_version_mismatch(self):
        # Use temporary files so this test does not modify the real repository.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools21cm").mkdir()
            (root / ".github/workflows").mkdir(parents=True)

            (root / ".github/workflows/build_base.yml").write_text(
                f"  TOOLS_21CM_VERSION: {self.version}\n"
            )

            # The function checks the build and version before reading variants.
            (root / "tools21cm/conda_build_config.yaml").write_text(
                "python:\n  - 3.12\n"
            )

            invalid_configs = [
                {"version": self.version, "build_number": True},
                {"version": self.version + ".999", "build_number": self.build},
            ]

            for config in invalid_configs:
                with self.subTest(config=config):
                    (root / "tools21cm/release.json").write_text(
                        json.dumps(config)
                    )
                    with self.assertRaises(ValueError):
                        release.release_identity(root)


if __name__ == "__main__":
    unittest.main()