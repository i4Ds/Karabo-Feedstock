#!/usr/bin/env python3
"""Plan and run non-publishing dependency compatibility checks.

This script is used by the pull-request workflow. It selects dependency groups
affected by a PR, builds their reviewed Conda recipes into an isolated local
channel, installs the resulting artifacts with the configured Python/NumPy
targets, runs smoke tests, and writes a Markdown report. It never uploads a
package or uses an Anaconda token.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
SCAN_MODULE_PATH = SCRIPT_DIR / "dependency_scan.py"


def _load_scan_module():
    spec = importlib.util.spec_from_file_location("dependency_scan", SCAN_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {SCAN_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


dependency_scan = _load_scan_module()
ScanError = dependency_scan.ScanError


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def _run_git(repo_root: Path, arguments: Sequence[str]) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ScanError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout


def _active_dependency(dependency: object) -> bool:
    return dependency.compatibility_mode not in {"manual", "retired"}


def _path_matches_dependency(path: str, dependency: object) -> bool:
    candidates = set(dependency.recipes) | set(dependency.validation_recipe_paths)
    for candidate in candidates:
        recipe_path = Path(candidate).as_posix()
        recipe_dir = Path(recipe_path).parent.as_posix()
        if path == recipe_path or path.startswith(f"{recipe_dir}/"):
            return True
    return False


def select_changed_dependencies(
    repo_root: Path,
    registry: object,
    base: str,
    head: str = "HEAD",
) -> list[object]:
    """Return active dependency groups changed between two Git refs."""

    changed_paths = {
        line.strip()
        for line in _run_git(repo_root, ["diff", "--name-only", f"{base}...{head}"]).splitlines()
        if line.strip()
    }

    changed_variables: set[str] = set()
    build_base = registry.settings.build_base
    if build_base in changed_paths:
        old_content = _run_git(repo_root, ["show", f"{base}:{build_base}"])
        new_content = (repo_root / build_base).read_text(encoding="utf-8")
        old_versions = dependency_scan.parse_build_versions(old_content)
        new_versions = dependency_scan.parse_build_versions(new_content)
        changed_variables = {
            variable
            for variable in set(old_versions) | set(new_versions)
            if old_versions.get(variable) != new_versions.get(variable)
        }

    selected: list[object] = []
    selected_ids: set[str] = set()
    for dependency in registry.dependencies:
        if not _active_dependency(dependency):
            continue
        variable_changed = bool(
            changed_variables.intersection(dependency.version_variables)
        )
        if variable_changed:
            selected.append(dependency)
            selected_ids.add(dependency.id)

    # A validation recipe can intentionally serve more than one dependency
    # group (for example, FINUFFT validates both FINUFFT and its FFTW pin). If a
    # version variable changed, that variable identifies the intended group and
    # prevents the same expensive recipe from being built twice. Recipe-only
    # changes still select every affected group because there is no such signal.
    for path in sorted(changed_paths):
        matching = [
            dependency
            for dependency in registry.dependencies
            if _active_dependency(dependency)
            and _path_matches_dependency(path, dependency)
        ]
        if any(dependency.id in selected_ids for dependency in matching):
            continue
        for dependency in matching:
            if dependency.id not in selected_ids:
                selected.append(dependency)
                selected_ids.add(dependency.id)
    return selected


def dependency_by_id(registry: object, dependency_id: str) -> object:
    for dependency in registry.dependencies:
        if dependency.id == dependency_id:
            if not _active_dependency(dependency):
                raise ScanError(
                    f"{dependency_id}: compatibility mode "
                    f"{dependency.compatibility_mode!r} is not automated"
                )
            return dependency
    raise ScanError(f"unknown dependency id: {dependency_id}")


def matrix_payload(dependencies: Sequence[object]) -> dict[str, object]:
    return {
        "include": [
            {
                "dependency": dependency.id,
                "name": dependency.name,
                "needs_cuda": dependency.needs_cuda,
            }
            for dependency in dependencies
        ]
    }


def write_github_outputs(path: Path, values: Mapping[str, str]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ScanError(f"GitHub output {key} must be a single line")
            stream.write(f"{key}={value}\n")


def parse_variant_values(content: str, key: str) -> tuple[str, ...]:
    """Read a simple top-level list from conda_build_config.yaml."""

    lines = content.splitlines()
    start: int | None = None
    key_indent = 0
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(\s*)" + re.escape(key) + r"\s*:\s*(?:#.*)?", line)
        if match:
            start = index + 1
            key_indent = len(match.group(1))
            break
    if start is None:
        return ()

    values: list[str] = []
    for line in lines[start:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= key_indent:
            break
        match = re.match(r"\s*-\s*([^#\s]+)", line)
        if match:
            values.append(match.group(1).strip("'\""))
    return tuple(values)


def _major_minor(value: str) -> tuple[int, int]:
    match = re.match(r"^(\d+)\.(\d+)", value)
    if not match:
        raise ScanError(f"expected a numeric major.minor version, got {value!r}")
    return int(match.group(1)), int(match.group(2))


def preflight_checks(
    repo_root: Path, registry: object, dependency: object
) -> tuple[list[CheckResult], list[str]]:
    results: list[CheckResult] = []
    warnings: list[str] = []
    python_variants: set[str] = set()
    numpy_variants: set[str] = set()

    for recipe in dependency.validation_recipe_paths:
        recipe_path = (repo_root / recipe).resolve()
        if not recipe_path.is_file():
            results.append(CheckResult("Recipe", "Failed", f"Missing {recipe}"))
            continue
        results.append(CheckResult("Recipe", "Passed", recipe))
        variant_path = recipe_path.parent / "conda_build_config.yaml"
        if variant_path.is_file():
            content = variant_path.read_text(encoding="utf-8")
            python_variants.update(parse_variant_values(content, "python"))
            numpy_variants.update(parse_variant_values(content, "numpy"))

    target_python = registry.compatibility.python_version
    target_numpy = registry.compatibility.numpy_version
    if dependency.compatibility_mode in {"python-only", "python-numpy"}:
        if target_python in python_variants:
            results.append(
                CheckResult("Python variant", "Passed", target_python)
            )
        else:
            results.append(
                CheckResult(
                    "Python variant",
                    "Failed",
                    f"Python {target_python} is absent from the recipe variant files",
                )
            )

    if dependency.compatibility_mode == "python-numpy":
        target_pair = _major_minor(target_numpy)
        configured_pairs = {_major_minor(value) for value in numpy_variants}
        if target_pair in configured_pairs:
            results.append(CheckResult("NumPy variant", "Passed", target_numpy))
        else:
            results.append(
                CheckResult(
                    "NumPy variant",
                    "Failed",
                    f"NumPy {target_pair[0]}.{target_pair[1]} is absent from "
                    "the recipe variant files",
                )
            )

    if dependency.compatibility_mode == "coinstall":
        warnings.append(
            "This native package has no direct Python import test; it is built and "
            "installed alongside the target Python and NumPy versions."
        )
    if not dependency.python_smoke_tests:
        warnings.append("No dependency-specific Python smoke test is configured.")
    return results, warnings


class CommandRunner:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        name: str,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CheckResult:
        shown = " ".join(command)
        print(f"::group::{name}\n$ {shown}")
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n## {name}\n$ {shown}\n")
            process = subprocess.Popen(
                list(command),
                cwd=cwd,
                env=dict(env),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="")
                log.write(line)
            return_code = process.wait()
        print("::endgroup::")
        status = "Passed" if return_code == 0 else "Failed"
        return CheckResult(name, status, f"exit code {return_code}")


def _environment_for_build(repo_root: Path, registry: object) -> dict[str, str]:
    build_base_path = repo_root / registry.settings.build_base
    versions = dependency_scan.parse_build_versions(
        build_base_path.read_text(encoding="utf-8")
    )
    environment = dict(os.environ)
    # The validator must remain non-publishing and should not expose a local
    # developer credential to upstream build code. GitHub Actions grants this
    # workflow no write token, and this filtering also protects manual runs.
    for key in tuple(environment):
        upper_key = key.upper()
        if any(
            marker in upper_key
            for marker in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
        ) or upper_key in {"SSH_AUTH_SOCK", "GPG_AGENT_INFO"}:
            environment.pop(key)
    for variable, value in versions.items():
        environment[variable] = value
        environment[f"{variable}_ALT"] = value
    environment["CHANNEL_LABEL"] = registry.compatibility.channel_label
    environment["build"] = "0"
    return environment


def _artifact_paths(build_root: Path, package_names: Sequence[str]) -> list[Path]:
    artifacts: list[Path] = []
    for package_name in package_names:
        matches = sorted(
            path
            for pattern in (f"{package_name}-*.conda", f"{package_name}-*.tar.bz2")
            for path in build_root.glob(f"**/{pattern}")
            if path.is_file()
        )
        if not matches:
            raise ScanError(f"no locally built artifact found for {package_name}")
        artifacts.append(matches[-1])
    return artifacts


def render_report(
    registry: object,
    dependency: object,
    results: Sequence[CheckResult],
    warnings: Sequence[str],
) -> str:
    failed = any(result.status == "Failed" for result in results)
    overall = "Failed" if failed else "Passed"
    lines = [
        f"# Compatibility report: {dependency.name}",
        "",
        f"**Overall result: {overall}**",
        "",
        f"- Compatibility mode: `{dependency.compatibility_mode}`",
        f"- Required Python: `{registry.compatibility.python_version}`",
        f"- Required NumPy: `{registry.compatibility.numpy_version}`",
        "- Package publication: **disabled**",
        "",
        "| Check | Result | Details |",
        "| --- | --- | --- |",
    ]
    for result in results:
        detail = result.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {result.name} | {result.status} | {detail} |")
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "This validation used only a local Conda build directory. It did not "
            "upload a package, create a release, approve the PR, or merge anything.",
            "",
        ]
    )
    return "\n".join(lines)


def append_step_summary(report: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(report)
    print(report)


def execute_validation(
    repo_root: Path,
    registry: object,
    dependency: object,
    report_path: Path,
    log_path: Path,
) -> int:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    results, warnings = preflight_checks(repo_root, registry, dependency)
    if any(result.status == "Failed" for result in results):
        report = render_report(registry, dependency, results, warnings)
        report_path.write_text(report, encoding="utf-8")
        append_step_summary(report)
        return 1

    runner = CommandRunner(log_path)
    environment = _environment_for_build(repo_root, registry)
    temporary_parent = environment.get("RUNNER_TEMP")
    build_root = Path(
        tempfile.mkdtemp(
            prefix=f"conda-bld-{dependency.id}-",
            dir=temporary_parent,
        )
    ).resolve()
    environment["CONDA_BLD_PATH"] = str(build_root)

    common = [
        "--no-anaconda-upload",
        "-c",
        f"i4ds/label/{registry.compatibility.channel_label}",
        "-c",
        "i4ds",
        "-c",
        "conda-forge",
    ]
    if dependency.compatibility_mode in {"python-only", "python-numpy"}:
        common.extend(["--python", registry.compatibility.python_version])
    if dependency.compatibility_mode == "python-numpy":
        major, minor = _major_minor(registry.compatibility.numpy_version)
        common.extend(["--numpy", f"{major}.{minor}"])

    for index, (recipe, package_name) in enumerate(
        zip(
            dependency.validation_recipe_paths,
            dependency.validation_package_names,
            strict=True,
        )
    ):
        recipe_environment = dict(environment)
        recipe_environment["PACKAGE_NAME"] = package_name
        local_channel = ["-c", build_root.as_uri()] if index else []
        result = runner.run(
            f"Build {package_name}",
            ["conda", "build", *local_channel, *common, recipe],
            cwd=repo_root,
            env=recipe_environment,
        )
        results.append(result)
        if result.status == "Failed":
            break

    if not any(result.status == "Failed" for result in results):
        try:
            artifacts = _artifact_paths(
                build_root, dependency.validation_package_names
            )
            results.append(
                CheckResult(
                    "Local artifacts",
                    "Passed",
                    ", ".join(path.name for path in artifacts),
                )
            )
        except ScanError as error:
            artifacts = []
            results.append(CheckResult("Local artifacts", "Failed", str(error)))

    environment_prefix = build_root / "compat-env"
    if not any(result.status == "Failed" for result in results):
        create_command = [
            "conda",
            "create",
            "-y",
            "-p",
            str(environment_prefix),
            f"python={registry.compatibility.python_version}",
        ]
        if dependency.compatibility_mode in {"python-numpy", "coinstall"}:
            create_command.append(f"numpy={registry.compatibility.numpy_version}")
        results.append(
            runner.run(
                "Create clean compatibility environment",
                create_command,
                cwd=repo_root,
                env=environment,
            )
        )

    if not any(result.status == "Failed" for result in results):
        install_command = [
            "conda",
            "install",
            "-y",
            "-p",
            str(environment_prefix),
            "-c",
            f"i4ds/label/{registry.compatibility.channel_label}",
            "-c",
            "i4ds",
            "-c",
            "conda-forge",
            *[str(path) for path in artifacts],
        ]
        results.append(
            runner.run(
                "Install locally built artifacts",
                install_command,
                cwd=repo_root,
                env=environment,
            )
        )

    if not any(result.status == "Failed" for result in results):
        results.append(
            runner.run(
                "Verify Python version",
                [
                    "conda",
                    "run",
                    "-p",
                    str(environment_prefix),
                    "python",
                    "-c",
                    (
                        "import sys; "
                        "assert sys.version_info[:2] == "
                        f"{tuple(_major_minor(registry.compatibility.python_version))!r}; "
                        "print(sys.version)"
                    ),
                ],
                cwd=repo_root,
                env=environment,
            )
        )

    if (
        dependency.compatibility_mode in {"python-numpy", "coinstall"}
        and not any(result.status == "Failed" for result in results)
    ):
        target_numpy = registry.compatibility.numpy_version
        results.append(
            runner.run(
                "Verify NumPy version",
                [
                    "conda",
                    "run",
                    "-p",
                    str(environment_prefix),
                    "python",
                    "-c",
                    (
                        "import numpy; "
                        f"assert numpy.__version__ == {target_numpy!r}, numpy.__version__; "
                        "print(numpy.__version__)"
                    ),
                ],
                cwd=repo_root,
                env=environment,
            )
        )

    for index, smoke_test in enumerate(dependency.python_smoke_tests, start=1):
        if any(result.status == "Failed" for result in results):
            break
        results.append(
            runner.run(
                f"Python smoke test {index}",
                [
                    "conda",
                    "run",
                    "-p",
                    str(environment_prefix),
                    "python",
                    "-c",
                    smoke_test,
                ],
                cwd=repo_root,
                env=environment,
            )
        )

    report = render_report(registry, dependency, results, warnings)
    report_path.write_text(report, encoding="utf-8")
    append_step_summary(report)
    return 1 if any(result.status == "Failed" for result in results) else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", default=".github/dependency-scan.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="create a changed-dependency matrix")
    plan.add_argument("--base")
    plan.add_argument("--head", default="HEAD")
    plan.add_argument("--dependency")
    plan.add_argument("--github-output", type=Path)

    validate = subparsers.add_parser("validate", help="run one compatibility check")
    validate.add_argument("--dependency", required=True)
    validate.add_argument(
        "--report", type=Path, default=Path("compatibility-report.md")
    )
    validate.add_argument(
        "--log", type=Path, default=Path("compatibility-validation.log")
    )
    validate.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate registry and recipe variants without invoking Conda",
    )
    return parser.parse_args(argv)


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    registry = dependency_scan.load_registry(repo_root / args.config)
    build_versions = dependency_scan.parse_build_versions(
        (repo_root / registry.settings.build_base).read_text(encoding="utf-8")
    )
    dependency_scan.validate_registry(registry, build_versions, repo_root)

    if args.command == "plan":
        if args.dependency:
            dependencies = [dependency_by_id(registry, args.dependency)]
        else:
            if not args.base:
                raise ScanError("plan requires --base or --dependency")
            dependencies = select_changed_dependencies(
                repo_root, registry, args.base, args.head
            )
        payload = matrix_payload(dependencies)
        serialized = json.dumps(payload, separators=(",", ":"))
        print(json.dumps(payload, indent=2))
        if args.github_output:
            write_github_outputs(
                args.github_output,
                {"matrix": serialized, "count": str(len(dependencies))},
            )
        return 0

    dependency = dependency_by_id(registry, args.dependency)
    if args.preflight_only:
        results, warnings = preflight_checks(repo_root, registry, dependency)
        report = render_report(registry, dependency, results, warnings)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        append_step_summary(report)
        return 1 if any(result.status == "Failed" for result in results) else 0
    return execute_validation(
        repo_root, registry, dependency, args.report, args.log
    )


def main() -> None:
    try:
        raise SystemExit(run())
    except (OSError, ScanError) as error:
        print(f"compatibility validation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
