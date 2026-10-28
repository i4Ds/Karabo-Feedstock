#!/usr/bin/env python3
"""Synchronize review-only upgrade candidate issues from the weekly scan issue.

Never builds, edits recipes, creates PRs, or publishes packages.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

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



def all_candidate_issues(client: GitHubIssueClient) -> dict[str, dict]:
    found = {}
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

            if dep_id in found:
                raise ScanError(
                    f"duplicate candidate issues found for {dep_id}"
                )

            found[dep_id] = issue

        if len(issues) < 100:
            break

        page += 1

    return found



def synchronize(client: GitHubIssueClient, registry, state: dict) -> list[str]:
    deps = {dep.id: dep for dep in registry.dependencies}
    existing = all_candidate_issues(client)
    changes = []
    for dep_id, entry in state["dependencies"].items():
        if entry["status"] != "update" or dep_id not in deps:
            continue
        dep = deps[dep_id]
        if dep.source_type in {"retired", "manual"}:
            continue
        latest = entry["latest"]
        if not latest:
            continue
        current = ", ".join(f"{k}={v}" for k, v in entry["configured"].items())
        body = candidate_body(dep, current, latest)
        title = f"[Upgrade candidate] {dep.name}: {current} → {latest}"
        found = existing.get(dep_id)
        if found is None:
            result = client._request("POST", "/issues", {"title": title, "body": body, "labels": [PENDING]})
            changes.append(f"Created candidate #{result['number']}: {dep_id}")
            continue
        if issue_version(found.get("body") or "") == latest:
            # Preserve the existing decision, including manual closure.
            # Do not reopen a candidate unless the detected version changes.
            continue
        number = found["number"]
        # A new version needs a new approval; replace only the decision labels.
        labels = [x["name"] for x in found.get("labels", []) if x["name"] not in {PENDING, APPROVED, DEFERRED}]
        labels.append(PENDING)
        client._request("PATCH", f"/issues/{number}", {
            "title": title, "body": body, "labels": labels, "state": "open",
        })
        changes.append(f"Updated candidate #{number} for {dep_id} to {latest}")
    return changes


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    registry = load_registry(root / ".github/dependency-scan.toml")
    build_path = root / registry.settings.build_base
    validate_registry(registry, parse_build_versions(build_path.read_text()), root)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo != registry.settings.expected_repository:
        raise ScanError("candidate sync requires the expected upstream repository")

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
        raise ScanError("tracking issue has no valid structured scan state")
    for name, color in LABELS.items():
        # Treat an existing GitHub label (HTTP 422) as already configured.
        try:
            client._request("POST", "/labels", {"name": name, "color": color})
        except Exception as exc:
            if getattr(exc, "status", None) != 422:
                raise
    for line in synchronize(client, registry, state):
        print(line)


if __name__ == "__main__":
    main()
