
#!/usr/bin/env python3
"""Prepare dependency upgrades for review.

Initial implementation: read-only AOtools upgrade planning.
Includes a safe-write helper, not yet enabled in the CLI.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote

from dependency_scan import (
    HttpClient,
    ScanError,
    load_registry,
    load_declared_versions,
    parse_stable_version,
    validate_registry,
)

def replace_version_variable(
    content: str,
    variable: str,
    current_version: str,
    target_version: str,
) -> str:
    """Replace exactly one expected version variable."""

    pattern = re.compile(
        rf"^([ \t]*{re.escape(variable)}:[ \t]*)"
        rf"({re.escape(current_version)})"
        rf"([ \t]*(?:#[^\n]*)?)$",
        re.MULTILINE,
    )

    updated, count = pattern.subn(
        lambda match: (
            match.group(1)
            + target_version
            + match.group(3)
        ),
        content,
    )

    if count != 1:
        raise ScanError(
            f"Expected exactly one {variable} declaration, "
            f"found {count}"
        )

    return updated


def replace_recipe_sha256(
    content: str,
    current_version: str,
    target_version: str,
    target_sha256: str,
) -> str:
    """Replace exactly one AOtools source checksum and version comment."""

    if not re.fullmatch(r"[0-9a-fA-F]{64}", target_sha256):
        raise ScanError("Invalid target SHA256 checksum")

    pattern = re.compile(
        rf"^([ \t]*sha256:[ \t]*)"
        rf"([0-9a-fA-F]{{64}})"
        rf"([ \t]+#[ \t]*{re.escape(current_version)}[ \t]+sdist[ \t]*)$",
        re.MULTILINE,
    )

    updated, count = pattern.subn(
        lambda match: (
            match.group(1)
            + target_sha256.lower()
            + f"  # {target_version} sdist"
        ),
        content,
    )

    if count != 1:
        raise ScanError(
            f"Expected exactly one SHA256 declaration for "
            f"AOtools {current_version}, found {count}"
        )

    return updated


def update_release_metadata(content: str, target_version: str) -> str:
    """Update the release version and reset the build number."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ScanError("Invalid release.json content") from exc

    if not isinstance(data, dict):
        raise ScanError("release.json must contain a JSON object")

    build_number = data.get("build_number")
    if type(build_number) is not int or build_number < 0:
        raise ScanError("Invalid build_number in release.json")

    if "version" not in data:
        raise ScanError("Missing version in release.json")

    data["version"] = target_version
    data["build_number"] = 0

    return json.dumps(data, indent=2) + "\n"


def show_diff(path: Path, original: str, updated: str) -> None:
    """Print a unified diff without modifying the file."""

    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )

    print("".join(diff), end="")


def apply_changes(
    changes: dict[Path, tuple[str, str]],
) -> None:
    """Stage validated updates and restore originals on write failure.

    This is a best-effort multi-file update, not a fully atomic
    transaction. Keep it disabled until its tests have passed.
    """

    staged: dict[Path, Path] = {}
    replaced: list[Path] = []

    try:
        # Verify every target still contains its expected content.
        for path, (original, _) in changes.items():
            if path.is_symlink() or not path.is_file():
                raise ScanError(f"Unsafe target file: {path}")

            if path.read_text(encoding="utf-8") != original:
                raise ScanError(f"File changed since planning: {path}")

        # Prepare every replacement before changing original files.
        for path, (_, updated) in changes.items():
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp:
                staged[path] = Path(temp.name)
                temp.write(updated)

            os.chmod(
                staged[path],
                path.stat().st_mode & 0o777,
            )

        # Apply replacements only after all files are staged.
        for path, temp_path in staged.items():
            os.replace(temp_path, path)
            replaced.append(path)

    except Exception:
        # Best-effort rollback for files already replaced.
        rollback_errors = []

        for path in reversed(replaced):
            original, _ = changes[path]
            rollback_path = None

            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=path.parent,
                    prefix=f".{path.name}.rollback.",
                    suffix=".tmp",
                    delete=False,
                ) as temp:
                    rollback_path = Path(temp.name)
                    temp.write(original)

                os.chmod(
                    rollback_path,
                    path.stat().st_mode & 0o777,
                )
                os.replace(rollback_path, path)

            except OSError as exc:
                rollback_errors.append(f"{path}: {exc}")

            finally:
                if rollback_path is not None:
                    rollback_path.unlink(missing_ok=True)

        if rollback_errors:
            raise ScanError(
                "Update failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            )

        raise

    finally:
        for temp_path in staged.values():
            temp_path.unlink(missing_ok=True)


def main(
    argv: list[str] | None = None,
    root: Path | None = None,
) -> None:
    parser = argparse.ArgumentParser(
        description="Plan an approved dependency upgrade."
    )

    parser.add_argument("--dependency", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply the prepared dependency upgrade",
    )
    args = parser.parse_args(argv)

    if root is None:
        root = Path(__file__).resolve().parents[2]

    registry = load_registry(root / ".github/dependency-scan.toml")

    build_versions = load_declared_versions(registry, root)
    validate_registry(registry, build_versions, root)

    dependencies = {
        dependency.id: dependency
        for dependency in registry.dependencies
    }

    dependency = dependencies.get(args.dependency)

    if dependency is None:
        raise ScanError("Unknown dependency")

    if dependency.id != "aotools":
        raise ScanError(
            "Only AOtools is supported in the initial implementation"
        )

    target = parse_stable_version(args.version)

    current = {
        variable: build_versions[variable]
        for variable in dependency.version_variables
    }

    if not all(
        target.key > parse_stable_version(version).key
        for version in current.values()
    ):
        raise ScanError(
            "Target version must be newer than the configured version"
        )

    http = HttpClient(
        timeout=registry.settings.http_timeout_seconds
    )

    filename, sha256 = get_pypi_sdist(
        dependency.package,
        target.text,
        http,
    )
    # Prepare the AOtools recipe checksum update in memory.
    recipe_path = root / "aotools/meta.yaml"
    original_recipe = recipe_path.read_text(encoding="utf-8")

    updated_recipe = replace_recipe_sha256(
        original_recipe,
        current["AOTOOLS_VERSION"],
        target.text,
        sha256,
    )

    # Prepare the AOtools build-number reset in memory.
    release_path = root / "aotools/release.json"
    original_release = release_path.read_text(encoding="utf-8")

    updated_release = update_release_metadata(
        original_release,
        target.text,
    )

    # Collect all validated changes before applying anything.
    changes = {
        recipe_path: (original_recipe, updated_recipe),
        release_path: (original_release, updated_release),
    }

    # Display the complete upgrade preview.
    for path, (original, updated) in changes.items():
        show_diff(
            path.relative_to(root),
            original,
            updated,
        )

    print(f"Dependency: {dependency.name}")
    print(f"Current versions: {current}")
    print(f"Target version: {target.text}")
    print(f"Recipes: {', '.join(dependency.recipes)}")
    print(f"Source archive: {filename}")
    print(f"PyPI SHA256: {sha256}")
    if args.write:
        apply_changes(changes)
        print("Mode: write")
        print("All prepared changes were applied.")
    else:
        print("Mode: read-only planning")
        print("No files were modified.")


def get_pypi_sdist(
    package: str,
    version: str,
    http: HttpClient,
) -> tuple[str, str]:
    """Return the verified PyPI sdist filename and SHA256 digest."""

    url = (
        f"https://pypi.org/pypi/"
        f"{quote(package, safe='')}/{quote(version, safe='')}/json"
    )

    data = http.get_json(url)

    if not isinstance(data, dict):
        raise ScanError("Unexpected PyPI response")

    files = data.get("urls")

    if not isinstance(files, list):
        raise ScanError("PyPI response contains no release files")

    expected_filename = f"{package}-{version}.tar.gz"

    matches = [
        item
        for item in files
        if isinstance(item, dict)
        and item.get("packagetype") == "sdist"
        and item.get("filename") == expected_filename
        and item.get("yanked") is False
    ]

    if len(matches) != 1:
        raise ScanError(
            f"Expected exactly one non-yanked source archive: "
            f"{expected_filename}"
        )

    digests = matches[0].get("digests")

    if not isinstance(digests, dict):
        raise ScanError("Missing PyPI digest metadata")

    digest = digests.get("sha256")

    if not isinstance(digest, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", digest
    ):
        raise ScanError("Missing or invalid SHA256 checksum")

    return expected_filename, digest.lower()


if __name__ == "__main__":
    main()
