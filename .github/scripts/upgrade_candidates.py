#!/usr/bin/env python3
"""Synchronize review-only upgrade candidate issues from the weekly scan issue.

Never builds, edits recipes, creates PRs, or publishes packages.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
import argparse

from dependency_scan import (
    GitHubIssueClient, ScanError, load_registry, parse_build_versions,
    read_scan_state, validate_registry, TRACKING_MARKER,
)

CANDIDATE_PREFIX = "<!-- karabo-upgrade-candidate:v1 "
VERSION_PREFIX = "<!-- karabo-upgrade-version:v1 "
PENDING = "upgrade:pending"
APPROVED = "upgrade:approved"
DEFERRED = "upgrade:deferred"
LABELS = {PENDING: "d4c5f9", APPROVED: "0e8a16", DEFERRED: "cccccc"}


def marker(dependency_id: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", dependency_id):
        raise ScanError("invalid dependency identifier")
    return f"{CANDIDATE_PREFIX}{dependency_id} -->"


def issue_version(body: str) -> str | None:
    match = re.search(r"<!-- karabo-upgrade-version:v1 ([0-9][0-9A-Za-z.+-]*) -->", body)
    return match.group(1) if match else None


def candidate_body(dependency, current: str, latest: str) -> str:
    return "\n".join([
        marker(dependency.id),
        f"{VERSION_PREFIX}{latest} -->",
        "## Dependency upgrade candidate", "",
        f"**Dependency:** {dependency.name} (`{dependency.id}`)",
        f"**Current:** `{current}`", f"**Candidate:** `{latest}`",
        f"**Upstream releases:** {dependency.release_page}",
        f"**Relevant recipes:** {', '.join(f'`{p}`' for p in dependency.recipes) or 'shared build configuration'}", "",
        "### Review before approval", "",
        "- [ ] Confirm Karabo-Pipeline or a Feedstock consumer still needs this dependency",
        "- [ ] Check Python 3.12 and NumPy 2.x compatibility (including compiled ABI)",
        "- [ ] Review AMD64/ARM64, CUDA/MPI and coupled dependencies where applicable",
        "- [ ] Check upstream release notes and existing patches", "",
        "### Decision", "",
        f"Apply `{APPROVED}` to authorize *preparation* of this upgrade, or `{DEFERRED}` to postpone it.",
        "Approving does **not** create a PR, run a package build, merge, or publish.",
        "After approval, launch the existing Karabo Feedstock Maintainer agent for this one dependency group.",
        "", f"**Registry notes:** {dependency.notes or 'None'}", "",
    ])



def all_candidate_issues(
    client: GitHubIssueClient,
    *,
    fresh: bool = False,
) -> dict[str, dict]:
    """Select one candidate per dependency, preserving historical issues."""
    open_candidates = {}
    closed_candidates = {}
    page = 1

    while True:
        issues = client._request(
            "GET",
            f"/issues?state=all&per_page=100&page={page}",
        )

        if not isinstance(issues, list):
            raise ScanError("GitHub issues API did not return a list")

        for issue in issues:
            if "pull_request" in issue:
                continue

            body = issue.get("body") or ""
            match = re.search(
                r"<!-- karabo-upgrade-candidate:v1 ([a-z0-9-]+) -->",
                body,
            )
            if not match:
                continue

            dep_id = match.group(1)
            creator = (issue.get("user") or {}).get("login", "")

            if creator != "github-actions[bot]":
                raise ScanError(
                    f"Untrusted candidate issue for {dep_id}"
                )

            if issue.get("state") == "open":
                if dep_id in open_candidates:
                    raise ScanError(
                        f"duplicate open candidate issues found for {dep_id}"
                    )
                open_candidates[dep_id] = issue

            elif issue.get("state") == "closed":
                # GitHub issue numbers increase over time.
                previous = closed_candidates.get(dep_id)
                if (
                    previous is None
                    or issue["number"] > previous["number"]
                ):
                    closed_candidates[dep_id] = issue
            else:
                raise ScanError(
                    f"Unexpected issue state for {dep_id}"
                )

        if len(issues) < 100:
            break

        page += 1

    if fresh:
        return open_candidates

    return {**closed_candidates, **open_candidates}


def synchronize(
    client: GitHubIssueClient,
    registry,
    state: dict,
    *,
    fresh: bool = False,
) -> list[str]:
    """Synchronize dependency upgrade candidates safely.

    Normal scan:
      - Preserve closed candidates for the same version.
      - Create a new issue when a newer candidate version appears.
      - Reuse existing open candidates.

    Fresh scan:
      - Ignore closed historical candidates.
      - Create new issues when no open candidate exists.
      - Never duplicate existing open candidates.

    Neither mode automatically approves upgrades or publishes packages.
    """
    deps = {dep.id: dep for dep in registry.dependencies}
    existing = all_candidate_issues(client, fresh=fresh)
    changes = []

    for dep_id, entry in state["dependencies"].items():

        # Only process dependencies with available updates.
        if entry["status"] != "update" or dep_id not in deps:
            continue

        dep = deps[dep_id]

        # Skip retired or manually managed dependencies.
        if dep.source_type in {"retired", "manual"}:
            continue

        latest = entry["latest"]
        if not latest:
            continue

        current = ", ".join(
            f"{key}={value}"
            for key, value in entry["configured"].items()
        )

        body = candidate_body(dep, current, latest)
        title = (
            f"[Upgrade candidate] {dep.name}: "
            f"{current} → {latest}"
        )

        found = existing.get(dep_id)

        # Closed issues are historical records.
        # Preserve a closed issue if the candidate version is unchanged.
        # Create a new issue if a different version is detected.
        if (
            found is not None
            and found["state"] == "closed"
            and issue_version(found.get("body") or "") != latest
        ):
            found = None

        # Create a new candidate if no applicable issue exists.
        if found is None:
            result = client._request(
                "POST",
                "/issues",
                {
                    "title": title,
                    "body": body,
                    "labels": [PENDING],
                },
            )

            changes.append(
                f"Created candidate #{result['number']}: {dep_id}"
            )
            continue

        existing_version = issue_version(
            found.get("body") or ""
        )

        # Preserve an existing candidate when its version matches.
        # This also preserves manual closure and approval decisions.
        if existing_version == latest:
            continue

        # An open candidate exists, but its version has changed.
        # Update it and require a new approval.
        number = found["number"]

        labels = [
            label["name"]
            for label in found.get("labels", [])
            if label["name"] not in {
                PENDING,
                APPROVED,
                DEFERRED,
            }
        ]
        labels.append(PENDING)

        client._request(
            "PATCH",
            f"/issues/{number}",
            {
                "title": title,
                "body": body,
                "labels": labels,
                "state": "open",
            },
        )

        changes.append(
            f"Updated candidate #{number} for {dep_id} to {latest}"
        )

    return changes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synchronize dependency upgrade candidate issues."
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Create new candidates while preserving closed historical issues.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    registry = load_registry(root / ".github/dependency-scan.toml")
    build_path = root / registry.settings.build_base

    validate_registry(
        registry,
        parse_build_versions(build_path.read_text()),
        root,
    )

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo != registry.settings.expected_repository:
        raise ScanError(
            "candidate sync requires the expected upstream repository"
        )

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ScanError("GITHUB_TOKEN is required")
    client = GitHubIssueClient(repo, token, 20)
    tracking = client.find_open_issue(TRACKING_MARKER)
    if tracking is None:
        print("No open dependency tracking issue; no candidates to synchronize")
        return
    state = read_scan_state(tracking.get("body") or "")
    if state is None:
        raise ScanError(
            "tracking issue has no valid structured scan state"
        )

    for name, color in LABELS.items():
        try:
            client._request(
                "POST",
                "/labels",
                {"name": name, "color": color},
            )
        except Exception as exc:
            if getattr(exc, "status", None) != 422:
                raise

    for line in synchronize(
        client,
        registry,
        state,
        fresh=args.fresh,
    ):
        print(line)


if __name__ == "__main__":
    main()
