
#!/usr/bin/env python3
"""Detect dependencies affected by a GitHub pull request."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent

# Reuse the existing dependency scanner and registry parser.
spec = importlib.util.spec_from_file_location(
    "dependency_scan", SCRIPT_DIR / "dependency_scan.py"
)
if spec is None or spec.loader is None:
    raise ImportError("Could not load dependency_scan.py")

scanner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scanner
spec.loader.exec_module(scanner)


# Changes to shared build configuration require broader validation.
SHARED_INFRASTRUCTURE = {
    ".github/workflows/build_base.yml",
    ".github/workflows/validate_dependency_pr.yml",
    ".github/dependency-scan.toml",
    ".github/scripts/dependency_scan.py",
    ".github/scripts/detect_changed_dependencies.py",
    "conda/conda_build_config.yaml",
}

# Dependencies no longer required by Karabo-Pipeline.
EXCLUDED_DEPENDENCIES = {
    "fftw3f",
    "rascil",
    "hvox",
    "pycsou",
    "katbeam",
    "montagepy",
    "bipp",
    "ska-gridder-nifty-cuda",
}

def git_output(*args: str) -> str:
    """Execute a Git command and return its output."""
    return subprocess.check_output(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
    )


def changed_files(base: str, head: str) -> set[str]:
    """Return files changed between two Git revisions."""
    output = git_output("diff", "--name-only", base, head)
    return set(output.splitlines())


def file_at_revision(revision: str, path: str) -> str:
    """Read a file from a specific Git revision."""
    return git_output("show", f"{revision}:{path}")


def without_version_lines(text: str) -> str:
    """Remove version-variable lines to identify shared workflow changes."""
    return "\n".join(
        line
        for line in text.splitlines()
        if not re.match(
            r"^\s*[A-Z][A-Z0-9_]*_VERSION\s*:",
            line,
        )
    )


def detect(base: str, head: str) -> dict[str, bool]:
    """Determine which registered dependencies need validation."""

    registry = scanner.load_registry(
        REPO_ROOT / ".github/dependency-scan.toml"
    )

    files = changed_files(base, head)
    affected: set[str] = set()

    # 1. Detect changes to dependency recipe directories.
    for dependency in registry.dependencies:
        recipe_directories = {
            str(Path(recipe).parent) + "/"
            for recipe in dependency.recipes
        }

        if any(
            file in dependency.recipes
            or any(
                file.startswith(directory)
                for directory in recipe_directories
            )
            for file in files
        ):
            affected.add(dependency.id)

    # 2. Detect changes to dependency versions in build_base.yml.
    build_base = registry.settings.build_base

    if build_base in files:
        before_text = file_at_revision(base, build_base)
        after_text = file_at_revision(head, build_base)

        before = scanner.parse_build_versions(before_text)
        after = scanner.parse_build_versions(after_text)

        changed_variables = {
            key
            for key in before.keys() | after.keys()
            if before.get(key) != after.get(key)
        }

        registered_variables = {
            variable
            for dependency in registry.dependencies
            for variable in dependency.version_variables
        }

        # Fail if a changed version variable is not registered.
        unknown_variables = changed_variables - registered_variables

        if unknown_variables:
            raise ValueError(
                "Unregistered version variables changed: "
                + ", ".join(sorted(unknown_variables))
            )

        # Map changed version variables to their dependencies.
        for dependency in registry.dependencies:
            if changed_variables.intersection(
                dependency.version_variables
            ):
                affected.add(dependency.id)

        # 3. Detect changes to shared build logic.
        # Version-only changes should not trigger every dependency.
        if without_version_lines(
            before_text
        ) != without_version_lines(after_text):
            affected.update(
                dependency.id
                for dependency in registry.dependencies
            )

    # 4. Detect changes to other shared infrastructure.
    shared_changes = files.intersection(
        SHARED_INFRASTRUCTURE - {build_base}
    )

    detector_test_changes = any(
        file.startswith(".github/scripts/tests/")
        for file in files
    )

    if shared_changes or detector_test_changes:
        affected.update(
            dependency.id
            for dependency in registry.dependencies
        )

    # 5. Detect changes to dependency-specific build workflows.
    for dependency in registry.dependencies:
        if files.intersection(dependency.validation_workflows):
            affected.add(dependency.id)

    # Keep dependency-specific release checks covered by centralized validation.
    dependency_validation_files = {
        ".github/workflows/validate_aotools_pr.yml": "aotools",
        ".github/scripts/tools21cm_release.py": "tools21cm",
        ".github/scripts/tests/test_tools21cm_release.py": "tools21cm",
    }

    for path, dependency_id in dependency_validation_files.items():
        if path in files:
            affected.add(dependency_id)

    # 6. Return validation decisions, excluding unused dependencies.
    return {
        dependency.id: (
            dependency.id in affected
            and dependency.id not in EXCLUDED_DEPENDENCIES
        )
        for dependency in registry.dependencies
    }

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect dependencies affected by a GitHub PR."
    )
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")

    args = parser.parse_args()

    result = detect(args.base, args.head)

    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
