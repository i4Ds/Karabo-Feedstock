
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


def prepare_dependency_version_updates(
    content: str,
    current_versions: dict[str, str],
    target_version: str,
) -> str:
    """Prepare version updates for all variables of one dependency."""

    if not current_versions:
        raise ScanError("Dependency has no version variables")
    if (
        set(current_versions) == {"OSKAR_VERSION", "OSKARPY_VERSION"}
        and len(set(current_versions.values())) != 1
    ):
        raise ScanError(
            "OSKAR_VERSION and OSKARPY_VERSION must be synchronized"
        )

    updated = content

    for variable, current_version in current_versions.items():
        updated = replace_version_variable(
            updated,
            variable,
            current_version,
            target_version,
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


def update_release_metadata(
    content: str,
    target_version: str,
    *,
    require_build_number: bool = True,
) -> str:
    """Update release metadata and reset the build number when required."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ScanError("Invalid release.json content") from exc

    if not isinstance(data, dict):
        raise ScanError("release.json must contain a JSON object")

    if require_build_number:
        build_number = data.get("build_number")
        if type(build_number) is not int or build_number < 0:
            raise ScanError("Invalid build_number in release.json")
    elif "build_number" in data:
        raise ScanError("Unexpected build_number in external release.json")

    if "version" not in data:
        raise ScanError("Missing version in release.json")

    data["version"] = target_version

    if require_build_number:
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


def verify_idg_tag(version: str, timeout: int = 20) -> None:
    """Verify the exact IDG tag exists in the upstream GitLab repository."""
    import subprocess

    repository = "https://gitlab.com/astron-idg/idg.git"
    ref = f"refs/tags/{version}"

    try:
        result = subprocess.run(
            [
                "git", "ls-remote", "--exit-code",
                "--refs", "--tags", repository, ref,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScanError(
            f"Could not verify IDG tag {version}: {exc}"
        ) from exc

    if result.returncode != 0:
        raise ScanError(
            f"IDG tag {version} could not be verified upstream"
        )

    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise ScanError(f"Unexpected Git response for IDG {version}")

    fields = lines[0].split()
    if (
        len(fields) != 2
        or not re.fullmatch(r"[0-9a-fA-F]{40,64}", fields[0])
        or fields[1] != ref
    ):
        raise ScanError(f"Unexpected Git tag reference for IDG {version}")


def prepare_idg_changes(
    root: Path,
    registry,
    dependency,
    current: dict[str, str],
    target: str,
) -> dict[Path, tuple[str, str]]:
    """Prepare a verified IDG version-only upgrade."""

    if (
        dependency.id != "idg"
        or dependency.source_type != "gitlab-tags"
        or tuple(dependency.version_variables) != ("IDG_VERSION",)
        or tuple(dependency.recipes) != ("idg/meta.yaml",)
        or dependency.version_file != "idg/release.json"
    ):
        raise ScanError("Unsupported IDG upgrade configuration")

    recipe_path = root / "idg/meta.yaml"
    recipe = recipe_path.read_text(encoding="utf-8")

    if not re.search(
        r"^[ \t]*git_url:[ \t]*https://gitlab\.com/astron-idg/idg\.git[ \t]*$",
        recipe,
        re.MULTILINE,
    ):
        raise ScanError("IDG recipe uses an unexpected Git repository")

    if not re.search(
        r"^[ \t]*git_tag:[ \t]*\{\{[ \t]*IDG_VERSION[ \t]*\}\}[ \t]*$",
        recipe,
        re.MULTILINE,
    ):
        raise ScanError("IDG recipe does not use the expected version tag")

    verify_idg_tag(
        target,
        timeout=registry.settings.http_timeout_seconds,
    )

    release_path = root / "idg/release.json"
    original = release_path.read_text(encoding="utf-8")

    updated = update_release_metadata(
        original,
        target,
    )

    return {release_path: (original, updated)}



def resolve_everybeam_tag(version: str, timeout: int = 20) -> str:
    """Resolve exact upstream vVERSION tag to its commit SHA."""
    import subprocess

    ref = f"refs/tags/v{version}"
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", "--tags",
             "https://git.astron.nl/RD/EveryBeam.git", ref, ref + "^{}"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScanError(f"Could not verify EveryBeam tag: {exc}") from exc

    if result.returncode != 0:
        raise ScanError(f"EveryBeam tag v{version} not found upstream")

    refs = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if (len(fields) != 2 or
                not re.fullmatch(r"[0-9a-fA-F]{40}", fields[0]) or
                fields[1] not in (ref, ref + "^{}") or
                fields[1] in refs):
            raise ScanError("Unexpected EveryBeam Git tag response")
        refs[fields[1]] = fields[0].lower()
    if ref not in refs:
        raise ScanError("Missing EveryBeam tag reference")
    return refs.get(ref + "^{}", refs[ref])


def prepare_everybeam_changes(root, registry, dependency, current, target):
    """Prepare release metadata and exact pinned upstream source update."""
    if (dependency.id != "everybeam" or
            dependency.source_type != "gitlab-tags" or
            tuple(dependency.version_variables) != ("EVERYBEAM_VERSION",) or
            tuple(dependency.recipes) != ("everybeam/meta.yaml",) or
            dependency.version_file != "everybeam/release.json"):
        raise ScanError("Unsupported EveryBeam upgrade configuration")

    recipe_path = root / "everybeam/meta.yaml"
    recipe = recipe_path.read_text(encoding="utf-8")
    if not re.search(
        r"^[ 	]*git_url:[ 	]*https://git\.astron\.nl/RD/EveryBeam\.git[ 	]*$",
        recipe, re.MULTILINE,
    ):
        raise ScanError("Unexpected EveryBeam Git repository")
    if len(re.findall(r"^[ 	]*git_rev:[ 	]*", recipe, re.MULTILINE)) != 1:
        raise ScanError("Expected exactly one EveryBeam git_rev")
    pattern = r"^([ 	]*git_rev:[ 	]*)([0-9a-fA-F]{40})([ 	]*)$"
    if not re.search(pattern, recipe, re.MULTILINE):
        raise ScanError("Expected a pinned EveryBeam source commit")

    release_path = root / "everybeam/release.json"
    original_release = release_path.read_text(encoding="utf-8")
    data = json.loads(original_release)
    if data.get("version") != current["EVERYBEAM_VERSION"]:
        raise ScanError("EveryBeam release metadata/version mismatch")

    sha = resolve_everybeam_tag(target, registry.settings.http_timeout_seconds)
    updated_recipe, count = re.subn(
        pattern, lambda m: m.group(1) + sha + m.group(3),
        recipe, count=1, flags=re.MULTILINE,
    )
    if count != 1:
        raise ScanError("Could not update EveryBeam source commit")
    return {
        recipe_path: (recipe, updated_recipe),
        release_path: (original_release, update_release_metadata(original_release, target)),
    }



def verify_wsclean_tag(version: str, timeout: int = 20) -> None:
    """Verify exact upstream vMAJOR.MINOR tag without trusting a branch."""
    import subprocess

    if not re.fullmatch(r"[0-9]+\.[0-9]+\.0", version):
        raise ScanError("WSClean requires a MAJOR.MINOR.0 version")
    upstream = version.rsplit(".", 1)[0]
    ref = f"refs/tags/v{upstream}"
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", "--refs", "--tags",
             "https://gitlab.com/aroffringa/wsclean.git", ref],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScanError(f"Could not verify WSClean tag: {exc}") from exc
    if result.returncode != 0:
        raise ScanError(f"WSClean tag v{upstream} not found upstream")
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise ScanError("Unexpected WSClean Git tag response")
    fields = lines[0].split()
    if (len(fields) != 2 or
            not re.fullmatch(r"[0-9a-fA-F]{40,64}", fields[0]) or
            fields[1] != ref):
        raise ScanError("Invalid WSClean Git tag response")


def prepare_wsclean_changes(root, registry, dependency, current, target):
    """Verify recipe and upstream tag; update release metadata only."""
    if (dependency.id != "wsclean" or
            dependency.source_type != "gitlab-tags" or
            dependency.base_url != "https://gitlab.com" or
            dependency.project != "aroffringa/wsclean" or
            tuple(dependency.version_variables) != ("WSCLEAN_VERSION",) or
            tuple(dependency.recipes) != ("wsclean/meta.yaml",) or
            dependency.version_file != "wsclean/release.json"):
        raise ScanError("Unsupported WSClean upgrade configuration")

    recipe = (root / "wsclean/meta.yaml").read_text(encoding="utf-8")
    if len(re.findall(r"^[ \t]*git_url:", recipe, re.MULTILINE)) != 1 or not re.search(
        r"^[ \t]*git_url:[ \t]*https://gitlab\.com/aroffringa/wsclean\.git[ \t]*$",
        recipe, re.MULTILINE,
    ):
        raise ScanError("Unexpected WSClean Git repository")
    if len(re.findall(r"^[ \t]*git_tag:", recipe, re.MULTILINE)) != 1 or not re.search(
        re.escape('git_tag: "v{{ WSCLEAN_VERSION[:-2] }}"') + r'[ 	]*$',
        recipe, re.MULTILINE,
    ):
        raise ScanError("Unexpected WSClean Git tag template")
    if not re.search(
        r"^[ \t]*version:[ \t]*\{\{[ \t]*WSCLEAN_VERSION_ALT[ \t]*\}\}[ \t]*$",
        recipe, re.MULTILINE,
    ):
        raise ScanError("Unexpected WSClean package version template")

    release_path = root / "wsclean/release.json"
    original = release_path.read_text(encoding="utf-8")
    try:
        data = json.loads(original)
    except json.JSONDecodeError as exc:
        raise ScanError("Invalid WSClean release metadata") from exc
    if not isinstance(data, dict) or data.get("version") != current["WSCLEAN_VERSION"]:
        raise ScanError("WSClean release metadata/version mismatch")

    verify_wsclean_tag(target, registry.settings.http_timeout_seconds)
    return {release_path: (original, update_release_metadata(original, target))}


def verify_oskar_tag(version: str, timeout: int = 20) -> None:
    """Verify the exact upstream tag, never a branch."""
    import subprocess
    ref = f"refs/tags/{version}"
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", "--refs", "--tags",
             "https://github.com/OxfordSKA/OSKAR.git", ref],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScanError(f"Could not verify OSKAR tag {version}: {exc}") from exc
    if result.returncode != 0:
        raise ScanError(f"OSKAR tag {version} not found upstream")
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise ScanError("Unexpected OSKAR Git response")
    fields = lines[0].split()
    if (len(fields) != 2 or
            not re.fullmatch(r"[0-9a-fA-F]{40,64}", fields[0]) or
            fields[1] != ref):
        raise ScanError("Invalid OSKAR Git tag response")


def prepare_oskar_changes(root, registry, dependency, current, target):
    """Validate both source recipes and prepare synchronized versions."""
    if (dependency.id != "oskar" or
            dependency.source_type != "github-tags" or
            tuple(dependency.version_variables) != ("OSKAR_VERSION", "OSKARPY_VERSION") or
            tuple(dependency.recipes) != ("oskar/meta.yaml", "oskar-py/meta.yaml") or
            dependency.version_file != "oskar/release.json"):
        raise ScanError("Unsupported OSKAR upgrade configuration")
    if current["OSKAR_VERSION"] != current["OSKARPY_VERSION"]:
        raise ScanError("OSKAR and OSKARPY versions must match")
    for recipe_name, var in (("oskar/meta.yaml", "OSKAR_VERSION"),
                             ("oskar-py/meta.yaml", "OSKARPY_VERSION")):
        recipe = (root / recipe_name).read_text(encoding="utf-8")
        if (recipe.count("git_url: https://github.com/OxfordSKA/OSKAR.git") != 1 or
                recipe.count("git_tag: {{ " + var + " }}") != 1):
            raise ScanError(f"Unexpected OSKAR source: {recipe_name}")
        if recipe_name == "oskar/meta.yaml":
            for patch_name in ("0001-fix-optional-phase-convention-tag.patch",
                               "0002-fix-strtok-r-with-old-glibc.patch"):
                if recipe.count("- " + patch_name) != 1 or not (root / "oskar" / patch_name).is_file():
                    raise ScanError(f"OSKAR patch missing or duplicated: {patch_name}")
    verify_oskar_tag(target, registry.settings.http_timeout_seconds)
    path = root / dependency.version_file
    original = path.read_text(encoding="utf-8")
    updated = update_release_metadata(original, target)
    return {path: (original, updated)}


def verify_fftw_tag(version: str, timeout: int = 20) -> None:
    """Verify the exact upstream FFTW release tag."""
    import subprocess

    ref = f"refs/tags/fftw-{version}"
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", "--refs", "--tags",
             "https://github.com/FFTW/fftw3.git", ref],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScanError(f"Could not verify FFTW tag {version}: {exc}") from exc
    if result.returncode != 0:
        raise ScanError(f"FFTW tag fftw-{version} not found upstream")
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise ScanError("Unexpected FFTW Git response")
    fields = lines[0].split()
    if (len(fields) != 2 or
            not re.fullmatch(r"[0-9a-fA-F]{40,64}", fields[0]) or
            fields[1] != ref):
        raise ScanError("Invalid FFTW Git tag response")


def prepare_fftw_changes(root, registry, dependency, current, target):
    """Prepare an independent FFTW dependency upgrade."""
    if (dependency.id != "fftw" or
            dependency.source_type != "github-tags" or
            dependency.repository != "FFTW/fftw3" or
            tuple(dependency.version_variables) != ("FFTW3_VERSION",) or
            tuple(dependency.recipes) != ("finufft/meta.yaml",) or
            dependency.version_file != "fftw/release.json"):
        raise ScanError("Unsupported FFTW upgrade configuration")
    recipe = (root / "finufft/meta.yaml").read_text(encoding="utf-8")
    pins = re.findall(
        r"^[ \t]*-[ \t]+fftw[ \t]+==[ \t]*\{\{[ \t]*"
        r"FFTW3_VERSION_ALT[ \t]*\}\}[ \t]*$", recipe, re.MULTILINE,
    )
    if len(pins) != 3:
        raise ScanError("Expected exactly three FFTW version pins in FINUFFT recipe")
    verify_fftw_tag(target, registry.settings.http_timeout_seconds)
    path = root / dependency.version_file
    original = path.read_text(encoding="utf-8")
    updated = update_release_metadata(
        original,
        target,
        require_build_number=False,
    )
    return {path: (original, updated)}


def verify_finufft_native_version(
    version: str,
    http: HttpClient,
) -> None:
    """Verify the native FINUFFT release version."""
    import base64
    import binascii

    url = (
        "https://api.github.com/repos/flatironinstitute/finufft/"
        f"contents/CMakeLists.txt?ref=v{quote(version, safe='')}"
    )

    data = http.get_json(url)

    if (
        not isinstance(data, dict)
        or data.get("encoding") != "base64"
        or not isinstance(data.get("content"), str)
    ):
        raise ScanError("Invalid FINUFFT native source response")

    try:
        source = base64.b64decode(
            "".join(data["content"].split()),
            validate=True,
        ).decode("utf-8")
    except (ValueError, binascii.Error) as exc:
        raise ScanError("Invalid FINUFFT CMakeLists.txt") from exc

    versions = re.findall(
        r"^\s*project\s*\(\s*FINUFFT\s+VERSION\s+"
        r"([0-9]+\.[0-9]+\.[0-9]+)\b",
        source,
        re.IGNORECASE | re.MULTILINE,
    )

    if versions != [version]:
        raise ScanError(
            f"FINUFFT native version mismatch: "
            f"expected {version}, found {versions}"
        )


def prepare_finufft_changes(root, registry, dependency, current, target):
    """Prepare a FINUFFT release metadata update."""
    if (dependency.id != "finufft" or
            dependency.source_type != "pypi" or
            dependency.package != "finufft" or
            tuple(dependency.version_variables) != ("FINUFFT_VERSION",) or
            tuple(dependency.recipes) != ("finufft/meta.yaml",) or
            dependency.version_file != "finufft/release.json"):
        raise ScanError("Unsupported FINUFFT upgrade configuration")

    path = root / dependency.version_file
    original = path.read_text(encoding="utf-8")
    http = HttpClient(
        timeout=registry.settings.http_timeout_seconds
    )

    verify_finufft_native_version(target, http)
    wheel = get_finufft_wheel(target, http)

    data = json.loads(update_release_metadata(original, target))
    data["wheel"] = wheel

    updated = json.dumps(data, indent=2) + "\n"

    return {path: (original, updated)}


def validate_upgrade_paths(
    root: Path,
    changes: dict[Path, tuple[str, str]],
) -> None:
    """Reject unsafe paths and automated workflow modifications."""
    repo_root = root.resolve()
    protected = repo_root / ".github/workflows"

    for path in changes:
        resolved = path.resolve()

        if not resolved.is_relative_to(repo_root):
            raise ScanError(
                f"Upgrade path is outside the repository: {path}"
            )

        relative = path.absolute().relative_to(root.absolute())

        if (
            relative.is_relative_to(Path(".github/workflows"))
            or resolved.is_relative_to(protected.resolve())
        ):
            raise ScanError(
                f"Automated upgrades must not modify workflow files: {path}"
            )


def prepare_pybdsf_changes(root, registry, dependency, current, target):
    """Verify the PyPI source archive and update only release metadata."""
    if (dependency.id != "pybdsf" or dependency.source_type != "pypi"
            or dependency.package != "bdsf"
            or tuple(dependency.version_variables) != ("PYBDSF_VERSION",)
            or tuple(dependency.recipes) != ("pybdsf/meta.yaml",)
            or dependency.version_file != "pybdsf/release.json"):
        raise ScanError("Unsupported PyBDSF upgrade configuration")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", target):
        raise ScanError("Unsupported PyBDSF version format")
    path = root / "pybdsf/release.json"
    original = path.read_text(encoding="utf-8")
    updated = json.loads(update_release_metadata(original, target))
    old = json.loads(original)
    if old["version"] != current["PYBDSF_VERSION"]:
        raise ScanError("PyBDSF release metadata/version mismatch")
    if not isinstance(old.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", old["sha256"]):
        raise ScanError("Invalid PyBDSF source checksum")
    recipe = (root / "pybdsf/meta.yaml").read_text(encoding="utf-8")
    if recipe.count('sha256: {{ release["sha256"] }}') != 1 or recipe.count('{{ PACKAGE_NAME }}-{{ PYBDSF_VERSION }}.tar.gz') != 1:
        raise ScanError("Unexpected PyBDSF recipe source")
    _, updated["sha256"] = get_pypi_sdist("bdsf", target, HttpClient(timeout=registry.settings.http_timeout_seconds))
    return {path: (original, json.dumps(updated, indent=2) + "\n")}


SKA_MODELS = {
    "ska-telmodel": ("SKA_TELMODEL_VERSION", "ska-telescope/ska-telmodel"),
    "ska-sdp-datamodels": ("SKA_SDP_DTMDL_VERSION", "ska-telescope/sdp/ska-sdp-datamodels"),
}


def ska_archive_digest(payload, package, version):
    import hashlib
    import io
    import tarfile
    from email.parser import BytesParser
    from packaging.utils import canonicalize_name
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            name = f"{package.replace('-', '_')}-{version}/PKG-INFO"
            members = [m for m in archive.getmembers() if m.name == name]
            if len(members) != 1 or not members[0].isfile() or members[0].size > 1024 * 1024:
                raise ScanError("Missing or invalid SKAO package metadata")
            metadata = BytesParser().parsebytes(archive.extractfile(members[0]).read())
            if (canonicalize_name(metadata.get("Name", "")) != package
                    or metadata.get("Version") != version):
                raise ScanError("SKAO archive identity mismatch")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ScanError("Invalid SKAO source archive") from exc
    return hashlib.sha256(payload).hexdigest()


def prepare_ska_models_changes(root, registry, dependency, current, target):
    import subprocess
    from urllib.request import urlopen
    variable, project = SKA_MODELS[dependency.id]
    package = dependency.id
    if (dependency.source_type != "gitlab-tags" or dependency.project != project
            or dependency.base_url != "https://gitlab.com"
            or tuple(dependency.version_variables) != (variable,)
            or tuple(dependency.recipes) != (f"{package}/meta.yaml",)
            or dependency.version_file != f"{package}/release.json"
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", target)):
        raise ScanError("Unsupported SKAO upgrade configuration")
    path = root / dependency.version_file
    original = path.read_text(encoding="utf-8")
    old = json.loads(original)
    updated = json.loads(update_release_metadata(original, target))
    if old["version"] != current[variable] or not re.fullmatch(r"[0-9a-f]{64}", str(old.get("sha256"))):
        raise ScanError("Invalid SKAO release metadata")
    timeout = registry.settings.http_timeout_seconds
    ref = f"refs/tags/{target}"
    try:
        result = subprocess.run(["git", "ls-remote", "--exit-code", "--refs", "--tags", f"https://gitlab.com/{project}.git", ref], capture_output=True, text=True, timeout=timeout, check=True)
        if not any(line.split()[1:] == [ref] for line in result.stdout.splitlines()):
            raise ScanError("Exact SKAO tag not found")
        filename = f"{package.replace('-', '_')}-{target}.tar.gz"
        url = f"https://artefact.skao.int/repository/pypi-internal/packages/{package}/{target}/{filename}"
        with urlopen(url, timeout=timeout) as response:
            payload = response.read(32 * 1024 * 1024 + 1)
        if len(payload) > 32 * 1024 * 1024:
            raise ScanError("SKAO archive exceeds size limit")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScanError(f"SKAO upstream verification failed: {exc}") from exc
    updated["sha256"] = ska_archive_digest(payload, package, target)
    return {path: (original, json.dumps(updated, indent=2) + "\n")}


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

    if dependency.id not in {"aotools", "idg", "everybeam", "wsclean", "oskar", "fftw", "finufft", "pybdsf", *SKA_MODELS}:
        raise ScanError(
            "No safe upgrade preparation strategy for this dependency"
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

    if dependency.id in SKA_MODELS:
        changes = prepare_ska_models_changes(
            root, registry, dependency, current, target.text
        )
        validate_upgrade_paths(root, changes)

        for path, (original, updated) in changes.items():
            show_diff(path.relative_to(root), original, updated)

        print(f"Dependency: {dependency.name}")
        print(f"Current versions: {current}")
        print(f"Target version: {target.text}")
        print("SKAO source archive: verified")

        if args.write:
            apply_changes(changes)
            print("Mode: write")
            print("All prepared changes were applied.")
        else:
            print("Mode: read-only planning")
            print("No files were modified.")
        return

    if dependency.id == "pybdsf":
        changes = prepare_pybdsf_changes(
            root, registry, dependency, current, target.text
        )
        validate_upgrade_paths(root, changes)

        for path, (original, updated) in changes.items():
            show_diff(path.relative_to(root), original, updated)

        print(f"Dependency: {dependency.name}")
        print(f"Current versions: {current}")
        print(f"Target version: {target.text}")
        print("PyBDSF source archive: verified")

        if args.write:
            apply_changes(changes)
            print("Mode: write")
            print("All prepared changes were applied.")
        else:
            print("Mode: read-only planning")
            print("No files were modified.")
        return

    if dependency.id == "finufft":
        changes = prepare_finufft_changes(
            root, registry, dependency, current, target.text
        )
        validate_upgrade_paths(root, changes)

        for path, (original, updated) in changes.items():
            show_diff(path.relative_to(root), original, updated)

        print(f"Dependency: {dependency.name}")
        print(f"Current versions: {current}")
        print(f"Target version: {target.text}")
        print("FINUFFT wheel and native version: verified")

        if args.write:
            apply_changes(changes)
            print("Mode: write")
            print("All prepared changes were applied.")
        else:
            print("Mode: read-only planning")
            print("No files were modified.")
        return

    if dependency.id == "fftw":
        changes = prepare_fftw_changes(root, registry, dependency, current, target.text)
        validate_upgrade_paths(root, changes)
        for path, (original, updated) in changes.items():
            show_diff(path.relative_to(root), original, updated)
        print(f"Dependency: {dependency.name}")
        print(f"Current versions: {current}")
        print(f"Target version: {target.text}")
        print("Upstream FFTW tag: verified")
        if args.write:
            apply_changes(changes)
            print("Mode: write")
            print("All prepared changes were applied.")
        else:
            print("Mode: read-only planning")
            print("No files were modified.")
        return

    if dependency.id == "oskar":
        changes = prepare_oskar_changes(root, registry, dependency, current, target.text)
        validate_upgrade_paths(root, changes)

        for path, (original, updated) in changes.items():
            show_diff(path.relative_to(root), original, updated)
        print(f"Dependency: {dependency.name}")
        print(f"Target version: {target.text}")
        print("Upstream OSKAR tag: verified")
        if args.write:
            apply_changes(changes)
            print("Mode: write")
        else:
            print("Mode: read-only planning")
            print("No files were modified.")
        return

    if dependency.id == "wsclean":
        # GitLab uses v3.7; Feedstock needs 3.7.0 for its recipe.
        if target.post is not None or len(target.release) not in (2, 3) or (
            len(target.release) == 3 and target.release[2] != 0
        ):
            raise ScanError("WSClean requires MAJOR.MINOR or MAJOR.MINOR.0")
        normalized = f"{target.release[0]}.{target.release[1]}.0"
        changes = prepare_wsclean_changes(
            root, registry, dependency, current, normalized,
        )
        validate_upgrade_paths(root, changes)

        for path, (original, updated) in changes.items():
            show_diff(path.relative_to(root), original, updated)
        print(f"Dependency: {dependency.name}")
        print(f"Target version: {normalized}")
        print("Upstream WSClean tag: verified")
        if args.write:
            apply_changes(changes)
            print("Mode: write")
            print("All prepared changes were applied.")
        else:
            print("Mode: read-only planning")
            print("No files were modified.")
        return

    if dependency.id == "everybeam":
        changes = prepare_everybeam_changes(
            root, registry, dependency, current, target.text,
        )
        validate_upgrade_paths(root, changes)

        for path, (original, updated) in changes.items():
            show_diff(path.relative_to(root), original, updated)
        print(f"Dependency: {dependency.name}")
        print(f"Target version: {target.text}")
        print("Upstream EveryBeam tag: verified")
        if args.write:
            apply_changes(changes)
            print("Mode: write")
            print("All prepared changes were applied.")
        else:
            print("Mode: read-only planning")
            print("No files were modified.")
        return

    if dependency.id == "idg":
        changes = prepare_idg_changes(
            root, registry, dependency, current, target.text,
        )

        validate_upgrade_paths(root, changes)

        for path, (original, updated) in changes.items():
            show_diff(path.relative_to(root), original, updated)

        print(f"Dependency: {dependency.name}")
        print(f"Current versions: {current}")
        print(f"Target version: {target.text}")
        print("Upstream IDG tag: verified")

        if args.write:
            apply_changes(changes)
            print("Mode: write")
            print("All prepared changes were applied.")
        else:
            print("Mode: read-only planning")
            print("No files were modified.")
        return

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

    validate_upgrade_paths(root, changes)

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


def get_finufft_wheel(
    version: str,
    http: HttpClient,
) -> dict[str, str]:
    """Select a non-yanked FINUFFT wheel for Python 3.12 on AMD64."""

    url = f"https://pypi.org/pypi/finufft/{quote(version, safe='')}/json"
    data = http.get_json(url)

    if not isinstance(data, dict):
        raise ScanError("Unexpected FINUFFT PyPI response")

    files = data.get("urls")

    if not isinstance(files, list):
        raise ScanError("FINUFFT release contains no files")

    suffix = "manylinux_2_17_x86_64.manylinux2014_x86_64.whl"

    filenames = {
        f"finufft-{version}-cp312-cp312-{suffix}",
        f"finufft-{version}-py3-none-{suffix}",
    }

    matches = [
        item
        for item in files
        if isinstance(item, dict)
        and item.get("packagetype") == "bdist_wheel"
        and item.get("filename") in filenames
        and item.get("yanked") is False
    ]

    if len(matches) != 1:
        raise ScanError(
            "Expected exactly one compatible FINUFFT wheel"
        )

    wheel = matches[0]

    from packaging.specifiers import SpecifierSet, InvalidSpecifier

    requires_python = wheel.get("requires_python")

    if not isinstance(requires_python, str) or not requires_python:
        raise ScanError("Missing FINUFFT Python requirements")

    try:
        compatible = SpecifierSet(requires_python).contains("3.12.0")
    except InvalidSpecifier as exc:
        raise ScanError("Invalid FINUFFT Python requirements") from exc

    if not compatible:
        raise ScanError("FINUFFT wheel does not support Python 3.12")

    filename = wheel["filename"]
    download_url = wheel.get("url")
    digests = wheel.get("digests")

    if not isinstance(digests, dict):
        raise ScanError("Missing FINUFFT wheel digests")

    digest = digests.get("sha256")

    if not isinstance(digest, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", digest
    ):
        raise ScanError("Invalid FINUFFT wheel SHA256")

    if not isinstance(download_url, str) or not re.fullmatch(
        r"https://files\.pythonhosted\.org/packages/"
        r"[0-9a-f/]+/" + re.escape(filename),
        download_url,
    ):
        raise ScanError("Invalid FINUFFT wheel download URL")

    return {
        "filename": filename,
        "url": download_url,
        "sha256": digest.lower(),
    }


if __name__ == "__main__":
    main()
