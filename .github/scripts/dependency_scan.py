#!/usr/bin/env python3
"""Read-only release scanner for the Karabo Feedstock.

The scanner compares versions declared in build_base.yml with trusted release
metadata configured in dependency-scan.toml. Its only remote write operation is
creating, updating, commenting on, assigning, or closing its own GitHub issue.
It never edits repository files, dispatches build workflows, or publishes Conda
packages.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import tomllib


USER_AGENT = "i4Ds-Karabo-Feedstock-dependency-scan/2"
TRACKING_MARKER = "<!-- karabo-dependency-scan:v2 -->"
TEST_MARKER = "<!-- karabo-dependency-scan-test:v2 -->"
DEFAULT_TAG_PATTERN = (
    r"^[vV]?(?P<version>\d+(?:\.\d+){0,3}(?:\.post\d+)?)$"
)
VERSION_VARIABLE_PATTERN = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*_VERSION)\s*:\s*"
    r"(?:\"([^\"]+)\"|'([^']+)'|([^\s#]+))",
    re.MULTILINE,
)
STABLE_VERSION_PATTERN = re.compile(
    r"^[vV]?(?P<release>\d+(?:\.\d+){0,3})(?:\.post(?P<post>\d+))?$",
    re.IGNORECASE,
)
ALLOWED_GITLAB_BASE_URLS = {"https://gitlab.com", "https://git.astron.nl"}
ALLOWED_SOURCE_TYPES = {
    "pypi",
    "github-tags",
    "gitlab-tags",
    "manual",
    "retired",
}


class ScanError(RuntimeError):
    """A controlled scanner failure suitable for a user-facing report."""


class GitHubApiError(ScanError):
    """A GitHub Issues API failure."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class StableVersion:
    """Small stable-version representation without a third-party dependency."""

    text: str
    release: tuple[int, ...]
    post: int | None = None

    @property
    def key(self) -> tuple[tuple[int, ...], int]:
        padded = self.release + (0,) * (4 - len(self.release))
        return padded, -1 if self.post is None else self.post


@dataclass(frozen=True)
class Settings:
    expected_repository: str
    build_base: str
    notify_user: str
    agent_name: str
    http_timeout_seconds: int
    max_pages: int
    max_workers: int


@dataclass(frozen=True)
class Dependency:
    id: str
    name: str
    version_variables: tuple[str, ...]
    recipes: tuple[str, ...]
    source_type: str
    release_page: str
    notes: str = ""
    manual_reason: str = ""
    retired_reason: str = ""
    package: str = ""
    repository: str = ""
    base_url: str = ""
    project: str = ""
    tag_pattern: str = DEFAULT_TAG_PATTERN
    ignored_versions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Registry:
    schema_version: int
    settings: Settings
    dependencies: tuple[Dependency, ...]


@dataclass(frozen=True)
class ScanResult:
    dependency: Dependency
    current_versions: Mapping[str, str]
    current: str
    latest: str | None
    status: str
    detail: str = ""


def parse_stable_version(value: str) -> StableVersion:
    """Parse numeric stable releases and reject prerelease/development labels."""

    candidate = value.strip()
    match = STABLE_VERSION_PATTERN.fullmatch(candidate)
    if not match:
        raise ScanError(f"unsupported or non-stable version: {value!r}")
    release_text = match.group("release")
    release = tuple(int(part) for part in release_text.split("."))
    post_text = match.group("post")
    post = int(post_text) if post_text is not None else None
    normalized = release_text + (f".post{post}" if post is not None else "")
    return StableVersion(text=normalized, release=release, post=post)


def versions_equal(left: str, right: str) -> bool:
    return parse_stable_version(left).key == parse_stable_version(right).key


def choose_latest_stable(
    candidates: Iterable[str], ignored_versions: Sequence[str] = ()
) -> StableVersion:
    ignored_keys: set[tuple[tuple[int, ...], int]] = set()
    for ignored in ignored_versions:
        ignored_keys.add(parse_stable_version(ignored).key)

    parsed: dict[tuple[tuple[int, ...], int], StableVersion] = {}
    for candidate in candidates:
        try:
            version = parse_stable_version(candidate)
        except ScanError:
            continue
        if version.key not in ignored_keys:
            parsed[version.key] = version

    if not parsed:
        raise ScanError("release source returned no supported stable versions")
    return parsed[max(parsed)]


def parse_build_versions(content: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for match in VERSION_VARIABLE_PATTERN.finditer(content):
        variable = match.group(1)
        value = next(group for group in match.groups()[1:] if group is not None)
        if variable in versions:
            raise ScanError(f"duplicate version variable in build_base.yml: {variable}")
        versions[variable] = value
    if not versions:
        raise ScanError("no *_VERSION variables found in build_base.yml")
    return versions


def _required_string(mapping: Mapping[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ScanError(f"{context}: {key} must be a non-empty string")
    return value.strip()


def _string_tuple(
    mapping: Mapping[str, Any], key: str, context: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    value = mapping.get(key)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ScanError(f"{context}: {key} must be a list of strings")
    result = tuple(item.strip() for item in value)
    if not allow_empty and not result:
        raise ScanError(f"{context}: {key} must not be empty")
    return result


def _optional_string_tuple(
    mapping: Mapping[str, Any], key: str, context: str
) -> tuple[str, ...]:
    if key not in mapping:
        return ()
    return _string_tuple(mapping, key, context, allow_empty=True)


def _bounded_int(
    mapping: Mapping[str, Any],
    key: str,
    context: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = mapping.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ScanError(f"{context}: {key} must be an integer")
    if not minimum <= value <= maximum:
        raise ScanError(
            f"{context}: {key} must be between {minimum} and {maximum}"
        )
    return value


def load_registry(path: Path) -> Registry:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ScanError(f"could not read registry {path}: {error}") from error

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        raise ScanError(f"unsupported dependency registry schema: {schema_version!r}")

    settings_raw = raw.get("settings")
    if not isinstance(settings_raw, dict):
        raise ScanError("registry: [settings] table is required")
    settings = Settings(
        expected_repository=_required_string(
            settings_raw, "expected_repository", "settings"
        ),
        build_base=_required_string(settings_raw, "build_base", "settings"),
        notify_user=_required_string(settings_raw, "notify_user", "settings"),
        agent_name=_required_string(settings_raw, "agent_name", "settings"),
        http_timeout_seconds=_bounded_int(
            settings_raw,
            "http_timeout_seconds",
            "settings",
            default=20,
            minimum=1,
            maximum=60,
        ),
        max_pages=_bounded_int(
            settings_raw,
            "max_pages",
            "settings",
            default=3,
            minimum=1,
            maximum=10,
        ),
        max_workers=_bounded_int(
            settings_raw,
            "max_workers",
            "settings",
            default=4,
            minimum=1,
            maximum=8,
        ),
    )

    dependencies_raw = raw.get("dependencies")
    if not isinstance(dependencies_raw, list) or not dependencies_raw:
        raise ScanError("registry: at least one [[dependencies]] table is required")

    dependencies: list[Dependency] = []
    for index, item in enumerate(dependencies_raw, start=1):
        context = f"dependencies[{index}]"
        if not isinstance(item, dict):
            raise ScanError(f"{context}: expected a table")
        source_type = _required_string(item, "source_type", context)
        dependency = Dependency(
            id=_required_string(item, "id", context),
            name=_required_string(item, "name", context),
            version_variables=_string_tuple(item, "version_variables", context),
            recipes=_string_tuple(item, "recipes", context, allow_empty=True),
            source_type=source_type,
            release_page=_required_string(item, "release_page", context),
            notes=str(item.get("notes", "")).strip(),
            manual_reason=str(item.get("manual_reason", "")).strip(),
            retired_reason=str(item.get("retired_reason", "")).strip(),
            package=str(item.get("package", "")).strip(),
            repository=str(item.get("repository", "")).strip(),
            base_url=str(item.get("base_url", "")).rstrip("/"),
            project=str(item.get("project", "")).strip(),
            tag_pattern=str(item.get("tag_pattern", DEFAULT_TAG_PATTERN)),
            ignored_versions=_optional_string_tuple(
                item, "ignored_versions", context
            ),
        )
        dependencies.append(dependency)

    return Registry(
        schema_version=schema_version,
        settings=settings,
        dependencies=tuple(dependencies),
    )


def _safe_repo_path(repo_root: Path, relative_path: str) -> Path:
    candidate = (repo_root / relative_path).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError as error:
        raise ScanError(f"registry path escapes repository: {relative_path}") from error
    return candidate


def validate_registry(
    registry: Registry, build_versions: Mapping[str, str], repo_root: Path
) -> None:
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_variables: set[str] = set()

    for dependency in registry.dependencies:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", dependency.id):
            errors.append(f"{dependency.id!r}: id must contain lowercase letters/numbers/hyphens")
        if dependency.id in seen_ids:
            errors.append(f"duplicate dependency id: {dependency.id}")
        seen_ids.add(dependency.id)

        if dependency.source_type not in ALLOWED_SOURCE_TYPES:
            errors.append(
                f"{dependency.id}: unsupported source_type {dependency.source_type!r}"
            )

        if not dependency.release_page.startswith("https://"):
            errors.append(f"{dependency.id}: release_page must use https")

        for variable in dependency.version_variables:
            if variable in seen_variables:
                errors.append(f"version variable registered more than once: {variable}")
            seen_variables.add(variable)
            if variable not in build_versions:
                errors.append(f"{dependency.id}: missing build variable {variable}")

        for recipe in dependency.recipes:
            try:
                path = _safe_repo_path(repo_root, recipe)
            except ScanError as error:
                errors.append(str(error))
                continue
            if not path.is_file():
                errors.append(f"{dependency.id}: recipe does not exist: {recipe}")

        if dependency.source_type == "pypi" and not dependency.package:
            errors.append(f"{dependency.id}: pypi source requires package")
        if dependency.source_type == "github-tags" and not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", dependency.repository
        ):
            errors.append(f"{dependency.id}: invalid GitHub repository")
        if dependency.source_type == "gitlab-tags":
            if dependency.base_url not in ALLOWED_GITLAB_BASE_URLS:
                errors.append(f"{dependency.id}: unapproved GitLab base_url")
            if not dependency.project:
                errors.append(f"{dependency.id}: gitlab-tags source requires project")
        if dependency.source_type == "manual" and not dependency.manual_reason:
            errors.append(f"{dependency.id}: manual source requires manual_reason")
        if dependency.source_type == "retired" and not dependency.retired_reason:
            errors.append(f"{dependency.id}: retired source requires retired_reason")

        if dependency.source_type in {"github-tags", "gitlab-tags"}:
            try:
                pattern = re.compile(dependency.tag_pattern)
                if "version" not in pattern.groupindex:
                    errors.append(
                        f"{dependency.id}: tag_pattern needs a named 'version' group"
                    )
            except re.error as error:
                errors.append(f"{dependency.id}: invalid tag_pattern: {error}")

        for ignored in dependency.ignored_versions:
            try:
                parse_stable_version(ignored)
            except ScanError as error:
                errors.append(f"{dependency.id}: invalid ignored version: {error}")

    declared = set(build_versions)
    missing_from_registry = sorted(declared - seen_variables)
    missing_from_build = sorted(seen_variables - declared)
    if missing_from_registry:
        errors.append(
            "unregistered build_base.yml variables: " + ", ".join(missing_from_registry)
        )
    if missing_from_build:
        errors.append(
            "registry variables absent from build_base.yml: " + ", ".join(missing_from_build)
        )

    if errors:
        raise ScanError("invalid dependency registry:\n- " + "\n- ".join(errors))


class HttpClient:
    def __init__(self, timeout: int, github_token: str = "") -> None:
        self.timeout = timeout
        self.github_token = github_token

    def get_json(self, url: str) -> Any:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self.github_token and url.startswith("https://api.github.com/"):
            headers["Authorization"] = f"Bearer {self.github_token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                request = Request(url, headers=headers)
                with urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except HTTPError as error:
                message = error.read(1000).decode("utf-8", errors="replace")
                last_error = ScanError(
                    f"GET {url} failed with HTTP {error.code}: {message[:300]}"
                )
                if error.code < 500 and error.code != 429:
                    break
            except (URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = ScanError(f"GET {url} failed: {error}")
            if attempt == 0:
                time.sleep(1)
        assert last_error is not None
        raise last_error


def _extract_tag_versions(dependency: Dependency, tag_names: Iterable[str]) -> list[str]:
    pattern = re.compile(dependency.tag_pattern)
    extracted: list[str] = []
    for tag_name in tag_names:
        match = pattern.fullmatch(tag_name.strip())
        if match:
            extracted.append(match.group("version"))
    return extracted


def _pypi_candidates(dependency: Dependency, http: HttpClient) -> list[str]:
    url = f"https://pypi.org/pypi/{quote(dependency.package, safe='')}/json"
    data = http.get_json(url)
    if not isinstance(data, dict):
        raise ScanError(f"{dependency.id}: unexpected PyPI response")

    candidates: list[str] = []
    releases = data.get("releases", {})
    if isinstance(releases, dict):
        for version, files in releases.items():
            if not isinstance(files, list) or not files:
                continue
            if any(not bool(file.get("yanked")) for file in files if isinstance(file, dict)):
                candidates.append(str(version))
    if not candidates:
        info = data.get("info", {})
        if isinstance(info, dict) and info.get("version"):
            candidates.append(str(info["version"]))
    return candidates


def _github_tag_candidates(
    dependency: Dependency, http: HttpClient, max_pages: int
) -> list[str]:
    tags: list[str] = []
    for page in range(1, max_pages + 1):
        query = urlencode({"per_page": 100, "page": page})
        url = f"https://api.github.com/repos/{dependency.repository}/tags?{query}"
        data = http.get_json(url)
        if not isinstance(data, list):
            raise ScanError(f"{dependency.id}: unexpected GitHub tags response")
        tags.extend(str(item.get("name", "")) for item in data if isinstance(item, dict))
        if len(data) < 100:
            break
    return _extract_tag_versions(dependency, tags)


def _gitlab_tag_candidates(
    dependency: Dependency, http: HttpClient, max_pages: int
) -> list[str]:
    tags: list[str] = []
    project = quote(dependency.project, safe="")
    for page in range(1, max_pages + 1):
        query = urlencode({"per_page": 100, "page": page})
        url = (
            f"{dependency.base_url}/api/v4/projects/{project}/repository/tags?{query}"
        )
        data = http.get_json(url)
        if not isinstance(data, list):
            raise ScanError(f"{dependency.id}: unexpected GitLab tags response")
        tags.extend(str(item.get("name", "")) for item in data if isinstance(item, dict))
        if len(data) < 100:
            break
    return _extract_tag_versions(dependency, tags)


def get_latest_version(
    dependency: Dependency, http: HttpClient, max_pages: int
) -> StableVersion:
    if dependency.source_type == "pypi":
        candidates = _pypi_candidates(dependency, http)
    elif dependency.source_type == "github-tags":
        candidates = _github_tag_candidates(dependency, http, max_pages)
    elif dependency.source_type == "gitlab-tags":
        candidates = _gitlab_tag_candidates(dependency, http, max_pages)
    else:
        raise ScanError(f"{dependency.id}: source is not automatic")
    return choose_latest_stable(candidates, dependency.ignored_versions)


def _current_display(current_versions: Mapping[str, str]) -> str:
    values = list(current_versions.values())
    if len(set(values)) == 1:
        return values[0]
    return ", ".join(f"{key}={value}" for key, value in current_versions.items())


def scan_dependency(
    dependency: Dependency,
    build_versions: Mapping[str, str],
    http: HttpClient,
    max_pages: int,
) -> ScanResult:
    current_versions = {
        variable: build_versions[variable] for variable in dependency.version_variables
    }
    current = _current_display(current_versions)

    if dependency.source_type == "manual":
        return ScanResult(
            dependency=dependency,
            current_versions=current_versions,
            current=current,
            latest=None,
            status="manual",
            detail=dependency.manual_reason,
        )

    if dependency.source_type == "retired":
        return ScanResult(
            dependency=dependency,
            current_versions=current_versions,
            current=current,
            latest=None,
            status="retired",
            detail=dependency.retired_reason,
        )

    try:
        parsed_current = [
            parse_stable_version(value) for value in current_versions.values()
        ]
        if len({version.key for version in parsed_current}) != 1:
            raise ScanError(
                "coupled version variables differ: "
                + ", ".join(f"{k}={v}" for k, v in current_versions.items())
            )
        latest = get_latest_version(dependency, http, max_pages)
        current_key = parsed_current[0].key
        if latest.key > current_key:
            status = "update"
            detail = dependency.notes
        elif latest.key == current_key:
            status = "current"
            detail = ""
        else:
            status = "ahead"
            detail = "Configured version is newer than the latest supported stable release detected."
        return ScanResult(
            dependency=dependency,
            current_versions=current_versions,
            current=current,
            latest=latest.text,
            status=status,
            detail=detail,
        )
    except Exception as error:  # keep one provider failure from hiding other results
        return ScanResult(
            dependency=dependency,
            current_versions=current_versions,
            current=current,
            latest=None,
            status="error",
            detail=str(error),
        )


def scan_all(
    registry: Registry, build_versions: Mapping[str, str], http: HttpClient
) -> list[ScanResult]:
    ordered: list[ScanResult | None] = [None] * len(registry.dependencies)
    with ThreadPoolExecutor(max_workers=registry.settings.max_workers) as pool:
        pending = {
            pool.submit(
                scan_dependency,
                dependency,
                build_versions,
                http,
                registry.settings.max_pages,
            ): index
            for index, dependency in enumerate(registry.dependencies)
        }
        for future in as_completed(pending):
            ordered[pending[future]] = future.result()
    return [result for result in ordered if result is not None]


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _status_label(status: str) -> str:
    return {
        "current": "Current",
        "update": "Update available",
        "manual": "Manual review",
        "retired": "Retired / not scanned",
        "ahead": "Configured newer",
        "error": "Scan error",
    }[status]


def render_summary(results: Sequence[ScanResult]) -> str:
    updates = sum(result.status == "update" for result in results)
    errors = sum(result.status == "error" for result in results)
    manual = sum(result.status == "manual" for result in results)
    retired = sum(result.status == "retired" for result in results)
    lines = [
        "# Weekly Feedstock dependency scan",
        "",
        f"Checked **{len(results)} dependency groups**: "
        f"**{updates} updates**, **{errors} errors**, "
        f"**{manual} manual-review entries**, **{retired} retired entries**.",
        "",
        "| Dependency | Configured | Latest stable | Status |",
        "| --- | --- | --- | --- |",
    ]
    for result in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(result.dependency.name),
                    f"`{_md(result.current)}`",
                    f"`{_md(result.latest)}`" if result.latest else "—",
                    _status_label(result.status),
                ]
            )
            + " |"
        )

    manual_results = [result for result in results if result.status == "manual"]
    if manual_results:
        lines.extend(["", "## Registered manual-review entries", ""])
        for result in manual_results:
            lines.append(
                f"- **{result.dependency.name}:** {_md(result.detail)} "
                f"([source]({result.dependency.release_page}))"
            )

    retired_results = [result for result in results if result.status == "retired"]
    if retired_results:
        lines.extend(["", "## Retired entries (not scanned)", ""])
        for result in retired_results:
            lines.append(f"- **{result.dependency.name}:** {_md(result.detail)}")

    error_results = [result for result in results if result.status == "error"]
    if error_results:
        lines.extend(["", "## Scan errors", ""])
        for result in error_results:
            lines.append(f"- **{result.dependency.name}:** `{_md(result.detail)}`")

    lines.extend(
        [
            "",
            "This workflow did not modify files, trigger builds, create a pull request, or publish packages.",
            "",
        ]
    )
    return "\n".join(lines)


def _run_url(repository: str) -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if run_id:
        return f"{server}/{repository}/actions/runs/{run_id}"
    return f"{server}/{repository}/actions"


def _updates_table(results: Sequence[ScanResult]) -> list[str]:
    lines = [
        "| Dependency | Configured | Latest stable | Release source |",
        "| --- | --- | --- | --- |",
    ]
    for result in results:
        lines.append(
            f"| {_md(result.dependency.name)} | `{_md(result.current)}` | "
            f"`{_md(result.latest or 'unknown')}` | "
            f"[releases]({result.dependency.release_page}) |"
        )
    return lines


def _agent_task(result: ScanResult, registry: Registry) -> list[str]:
    dependency = result.dependency
    variables = ", ".join(dependency.version_variables)
    recipes = ", ".join(dependency.recipes) or "shared version pins in build_base.yml"
    lines = [
        f"<details><summary>Agent task: {dependency.name}</summary>",
        "",
        "```text",
        f"Use the {registry.settings.agent_name} custom agent.",
        f"Analyze the {dependency.name} update from {result.current} to {result.latest}.",
        f"Configured version variables: {variables}.",
        f"Relevant recipe files: {recipes}.",
        "Read the matching entry in .github/dependency-scan.toml and inspect the",
        "upstream release before changing anything. Explain the smallest safe change.",
        "If compatible, update all related Feedstock version fields, source revisions,",
        "checksums and build numbers. Validate without uploading packages, then create",
        "a draft pull request for human review. Never merge or publish packages.",
    ]
    if dependency.notes:
        lines.append(f"Special review note: {dependency.notes}")
    lines.extend(["```", "", "</details>", ""])
    return lines


def render_tracking_issue(results: Sequence[ScanResult], registry: Registry) -> tuple[str, str]:
    updates = [result for result in results if result.status == "update"]
    errors = [result for result in results if result.status == "error"]
    manual = [result for result in results if result.status == "manual"]
    retired = [result for result in results if result.status == "retired"]

    title_parts: list[str] = []
    if updates:
        title_parts.append(f"{len(updates)} update{'s' if len(updates) != 1 else ''}")
    if errors:
        title_parts.append(f"{len(errors)} scan error{'s' if len(errors) != 1 else ''}")
    title = "[Dependency scan] " + ", ".join(title_parts)

    lines = [
        TRACKING_MARKER,
        f"@{registry.settings.notify_user}",
        "",
        "## Weekly Feedstock dependency scan",
        "",
        f"The scanner checked {len(results)} configured dependency groups. "
        f"See the [workflow run]({_run_url(registry.settings.expected_repository)}).",
        "",
    ]
    if updates:
        lines.extend(["### Available updates", "", *_updates_table(updates), ""])
        lines.extend(
            [
                "Start one custom-agent session per dependency. "
                "OSKAR/OSKARPY and other explicitly grouped variables remain one task.",
                "",
            ]
        )
        for result in updates:
            lines.extend(_agent_task(result, registry))

    if errors:
        lines.extend(["### Scan errors", ""])
        for result in errors:
            lines.append(f"- **{result.dependency.name}:** `{_md(result.detail)}`")
        lines.append("")

    if manual:
        names = ", ".join(result.dependency.name for result in manual)
        lines.extend(
            [
                "### Manual-review coverage",
                "",
                f"The complete registry also tracks {len(manual)} intentionally manual "
                f"entries: {names}. Their versions are visible in the Actions summary, "
                "but the scanner does not guess a latest compatible version.",
                "",
            ]
        )

    if retired:
        names = ", ".join(result.dependency.name for result in retired)
        lines.extend(
            [
                "### Retired coverage",
                "",
                f"The registry retains {len(retired)} retired entries for coverage: "
                f"{names}. The scanner does not check releases or create agent tasks "
                "for them.",
                "",
            ]
        )

    lines.extend(
        [
            "### Safety",
            "",
            "This scan did **not** modify repository files, trigger a Conda build, "
            "publish a package, create a pull request, or merge anything.",
        ]
    )
    return title, "\n".join(lines)


def render_test_issue(results: Sequence[ScanResult], registry: Registry) -> tuple[str, str]:
    updates = [result for result in results if result.status == "update"]
    errors = [result for result in results if result.status == "error"]
    manual = [result for result in results if result.status == "manual"]
    retired = [result for result in results if result.status == "retired"]
    body_lines = [
        TEST_MARKER,
        f"@{registry.settings.notify_user}",
        "",
        "## Safe whole-feedstock notification test",
        "",
        "This issue was created because the workflow was run manually in "
        "`test-notification` mode.",
        "",
        f"- Dependency groups checked: **{len(results)}**",
        f"- Real updates currently detected: **{len(updates)}**",
        f"- Source errors: **{len(errors)}**",
        f"- Intentionally manual entries: **{len(manual)}**",
        f"- Retired entries not scanned: **{len(retired)}**",
        "",
    ]
    if updates:
        body_lines.extend(["### Updates found during the test", "", *_updates_table(updates), ""])
    if errors:
        body_lines.extend(["### Source errors found during the test", ""])
        for result in errors:
            body_lines.append(f"- **{result.dependency.name}:** `{_md(result.detail)}`")
        body_lines.append("")
    body_lines.extend(
        [
            "No recipe was changed, no build or custom agent was triggered, and no "
            "package or pull request was created.",
            "",
            "Close this issue after confirming that assignment and notification worked.",
        ]
    )
    return "[Test] Whole-feedstock dependency scan notification", "\n".join(body_lines)


class GitHubIssueClient:
    def __init__(self, repository: str, token: str, timeout: int) -> None:
        if not token:
            raise ScanError("GITHUB_TOKEN is required for issue operations")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ScanError("invalid GitHub repository name")
        self.repository = repository
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Any:
        url = f"https://api.github.com/repos/{self.repository}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content = response.read()
            return json.loads(content) if content else None
        except HTTPError as error:
            message = error.read(1000).decode("utf-8", errors="replace")
            raise GitHubApiError(
                error.code,
                f"GitHub {method} {path} failed with HTTP {error.code}: {message[:300]}",
            ) from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise GitHubApiError(0, f"GitHub {method} {path} failed: {error}") from error

    def find_open_issue(self, marker: str) -> Mapping[str, Any] | None:
        for page in range(1, 11):
            query = urlencode({"state": "open", "per_page": 100, "page": page})
            issues = self._request("GET", f"/issues?{query}")
            if not isinstance(issues, list):
                raise GitHubApiError(0, "GitHub issues response was not a list")
            for issue in issues:
                if (
                    isinstance(issue, dict)
                    and "pull_request" not in issue
                    and marker in str(issue.get("body", ""))
                ):
                    return issue
            if len(issues) < 100:
                break
        return None

    def _create_assigned(self, title: str, body: str, notify_user: str) -> int:
        try:
            created = self._request(
                "POST",
                "/issues",
                {"title": title, "body": body, "assignees": [notify_user]},
            )
        except GitHubApiError as error:
            if error.status != 422:
                raise
            print(
                f"Warning: could not assign @{notify_user}; creating mentioned issue without assignment.",
                file=sys.stderr,
            )
            created = self._request("POST", "/issues", {"title": title, "body": body})
        if not isinstance(created, dict) or not isinstance(created.get("number"), int):
            raise GitHubApiError(0, "created issue response did not contain a number")
        return int(created["number"])

    def upsert(self, marker: str, title: str, body: str, notify_user: str) -> int:
        existing = self.find_open_issue(marker)
        if existing is None:
            number = self._create_assigned(title, body, notify_user)
            print(f"Created issue #{number}")
            return number

        number = int(existing["number"])
        changed = existing.get("title") != title or existing.get("body") != body
        if changed:
            self._request("PATCH", f"/issues/{number}", {"title": title, "body": body})
            self._request(
                "POST",
                f"/issues/{number}/comments",
                {"body": f"@{notify_user}, the weekly scan found updated release information."},
            )
            print(f"Updated issue #{number}")
        else:
            print(f"Issue #{number} already contains the current scan results")

        assignees = existing.get("assignees", [])
        assigned_logins = {
            str(user.get("login")) for user in assignees if isinstance(user, dict)
        }
        if notify_user not in assigned_logins:
            try:
                self._request(
                    "POST",
                    f"/issues/{number}/assignees",
                    {"assignees": [notify_user]},
                )
            except GitHubApiError as error:
                print(f"Warning: could not assign @{notify_user}: {error}", file=sys.stderr)
        return number

    def close_if_open(self, marker: str, comment: str) -> int | None:
        existing = self.find_open_issue(marker)
        if existing is None:
            return None
        number = int(existing["number"])
        self._request("POST", f"/issues/{number}/comments", {"body": comment})
        self._request("PATCH", f"/issues/{number}", {"state": "closed"})
        print(f"Closed issue #{number}")
        return number


def _write_summary(summary: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(summary)
    print(summary)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(), help="Karabo-Feedstock checkout"
    )
    parser.add_argument(
        "--config",
        default=".github/dependency-scan.toml",
        help="registry path relative to --repo-root",
    )
    parser.add_argument(
        "--mode", choices=("scan", "test-notification"), default="scan"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan and report without creating, updating, or closing issues",
    )
    return parser.parse_args(argv)


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    config_path = _safe_repo_path(repo_root, args.config)
    registry = load_registry(config_path)
    build_base_path = _safe_repo_path(repo_root, registry.settings.build_base)
    build_versions = parse_build_versions(build_base_path.read_text(encoding="utf-8"))
    validate_registry(registry, build_versions, repo_root)

    repository = os.environ.get(
        "GITHUB_REPOSITORY", registry.settings.expected_repository
    )
    if repository != registry.settings.expected_repository:
        raise ScanError(
            f"refusing issue operations outside {registry.settings.expected_repository}: "
            f"running in {repository}"
        )

    token = os.environ.get("GITHUB_TOKEN", "")
    http = HttpClient(
        timeout=registry.settings.http_timeout_seconds,
        github_token=token,
    )
    results = scan_all(registry, build_versions, http)
    _write_summary(render_summary(results))

    updates = [result for result in results if result.status == "update"]
    errors = [result for result in results if result.status == "error"]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if args.dry_run:
        print("Dry run: no GitHub issue operation performed")
        return 1 if errors else 0

    issues = GitHubIssueClient(
        repository=repository,
        token=token,
        timeout=registry.settings.http_timeout_seconds,
    )
    if args.mode == "test-notification":
        title, body = render_test_issue(results, registry)
        issues.upsert(TEST_MARKER, title, body, registry.settings.notify_user)
        return 0

    if updates or errors:
        title, body = render_tracking_issue(results, registry)
        issues.upsert(TRACKING_MARKER, title, body, registry.settings.notify_user)
    else:
        issues.close_if_open(
            TRACKING_MARKER,
            f"All automatically monitored dependencies were current at {timestamp}. "
            "Closing this tracking issue.",
        )
        print("No updates or scan errors; no notification issue needed")

    return 1 if errors else 0


def main() -> None:
    try:
        raise SystemExit(run())
    except (OSError, ScanError) as error:
        print(f"dependency scan failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
