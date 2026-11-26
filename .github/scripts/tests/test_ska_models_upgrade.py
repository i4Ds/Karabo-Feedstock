"""Tests for automated SKA model dependency upgrades."""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_dependency_upgrade as planner


def make_archive(package, version, metadata_name=None,
                 metadata_version=None):
    """Create an in-memory SKAO-style source archive."""
    payload = io.BytesIO()

    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {metadata_name or package}\n"
        f"Version: {metadata_version or version}\n"
    ).encode()

    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        member = tarfile.TarInfo(
            f"{package.replace('-', '_')}-{version}/PKG-INFO"
        )
        member.size = len(metadata)
        archive.addfile(member, io.BytesIO(metadata))

    return payload.getvalue()


class TestSKAModelsUpgrade(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        self.registry = SimpleNamespace(
            settings=SimpleNamespace(http_timeout_seconds=20)
        )

    def configure(self, package="ska-telmodel"):
        variable, project = planner.SKA_MODELS[package]

        self.current_version = (
            "1.33.0" if package == "ska-telmodel" else "1.4.1"
        )
        self.target = (
            "1.34.0" if package == "ska-telmodel" else "3.0.0"
        )

        self.path = self.root / package / "release.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self.original = json.dumps({
            "version": self.current_version,
            "build_number": 1,
            "sha256": "a" * 64,
        })

        self.path.write_text(self.original)

        self.dependency = SimpleNamespace(
            id=package,
            source_type="gitlab-tags",
            project=project,
            base_url="https://gitlab.com",
            version_variables=(variable,),
            recipes=(f"{package}/meta.yaml",),
            version_file=f"{package}/release.json",
        )

        self.current = {variable: self.current_version}
        self.payload = make_archive(package, self.target)

    def prepare(self):
        return planner.prepare_ska_models_changes(
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
        for package in ("ska-telmodel", "ska-sdp-datamodels"):
            with self.subTest(package=package):
                self.configure(package)

                with patch(
                    "subprocess.run", return_value=self.valid_tag()
                ), patch(
                    "urllib.request.urlopen",
                    return_value=io.BytesIO(self.payload),
                ):
                    changes = self.prepare()

                self.assertEqual(set(changes), {self.path})
                self.assertEqual(
                    json.loads(changes[self.path][1]),
                    {
                        "version": self.target,
                        "build_number": 0,
                        "sha256": hashlib.sha256(
                            self.payload
                        ).hexdigest(),
                    },
                )

                self.assertEqual(
                    self.path.read_text(), self.original
                )

                planner.validate_upgrade_paths(
                    self.root, changes
                )

    def test_missing_tag(self):
        self.configure()

        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(2, "git"),
        ), patch("urllib.request.urlopen") as download:
            with self.assertRaises(planner.ScanError):
                self.prepare()

            download.assert_not_called()

    def test_wrong_tag(self):
        self.configure()

        result = self.valid_tag()
        result.stdout = "abc\trefs/tags/0.0.1\n"

        with patch(
            "subprocess.run", return_value=result
        ), patch("urllib.request.urlopen") as download:
            with self.assertRaises(planner.ScanError):
                self.prepare()

            download.assert_not_called()

    def test_missing_archive(self):
        self.configure()

        with patch(
            "subprocess.run", return_value=self.valid_tag()
        ), patch(
            "urllib.request.urlopen",
            side_effect=HTTPError(
                "https://artefact.skao.int",
                404, "Not found", None, None,
            ),
        ):
            with self.assertRaises(planner.ScanError):
                self.prepare()

    def test_invalid_archive(self):
        with self.assertRaises(planner.ScanError):
            planner.ska_archive_digest(
                b"invalid archive", "ska-telmodel", "1.34.0"
            )

    def test_wrong_package_name(self):
        payload = make_archive(
            "ska-telmodel", "1.34.0",
            metadata_name="wrong-package",
        )

        with self.assertRaises(planner.ScanError):
            planner.ska_archive_digest(
                payload, "ska-telmodel", "1.34.0"
            )

    def test_wrong_package_version(self):
        payload = make_archive(
            "ska-telmodel", "1.34.0",
            metadata_version="1.0.0",
        )

        with self.assertRaises(planner.ScanError):
            planner.ska_archive_digest(
                payload, "ska-telmodel", "1.34.0"
            )

    def test_invalid_existing_checksum(self):
        self.configure()

        metadata = json.loads(self.original)
        metadata["sha256"] = "invalid"
        self.path.write_text(json.dumps(metadata))

        with patch("subprocess.run") as git:
            with self.assertRaises(planner.ScanError):
                self.prepare()

            git.assert_not_called()

    def test_invalid_registry_configuration(self):
        self.configure()
        self.dependency.project = "wrong/project"

        with patch("subprocess.run") as git:
            with self.assertRaises(planner.ScanError):
                self.prepare()

            git.assert_not_called()

    def test_invalid_target_version(self):
        self.configure()
        self.target = "invalid-version"

        with patch("subprocess.run") as git:
            with self.assertRaises(planner.ScanError):
                self.prepare()

            git.assert_not_called()

    def test_workflow_modification_rejected(self):
        path = self.root / ".github/workflows/build_base.yml"

        with self.assertRaises(planner.ScanError):
            planner.validate_upgrade_paths(
                self.root, {path: ("old", "new")}
            )

    def test_external_path_rejected(self):
        path = self.root.parent / "release.json"

        with self.assertRaises(planner.ScanError):
            planner.validate_upgrade_paths(
                self.root, {path: ("old", "new")}
            )


if __name__ == "__main__":
    unittest.main()
