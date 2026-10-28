#!/usr/bin/env python3
"""Fail-closed approval audit; intentionally does not start an AI agent or build."""
from __future__ import annotations

import json
import os
from pathlib import Path

from dependency_scan import GitHubIssueClient, ScanError, TRACKING_MARKER, load_registry, read_scan_state
from upgrade_candidates import APPROVED, marker, issue_version


def validate_approval(event: dict, client: GitHubIssueClient, registry) -> str:
    issue = event.get("issue") or {}
    actor = (event.get("sender") or {}).get("login", "")
    if event.get("action") != "labeled" or (event.get("label") or {}).get("name") != APPROVED:
        raise ScanError("not an approval-label event")
    if "pull_request" in issue or not isinstance(issue.get("number"), int):
        raise ScanError("approval must target an issue")
    permission = client._request("GET", f"/collaborators/{actor}/permission").get("permission")
    if permission not in {"write", "maintain", "admin"}:
        raise ScanError(f"actor {actor!r} does not have repository write access")
    body = issue.get("body") or ""
    matching = [d for d in registry.dependencies if marker(d.id) in body]
    if len(matching) != 1 or matching[0].source_type in {"retired", "manual"}:
        raise ScanError("candidate issue does not identify one active registered dependency")
    dependency = matching[0]
    version = issue_version(body)
    tracking = client.find_open_issue(TRACKING_MARKER)
    if not tracking:
        raise ScanError("no active scan tracking issue")
    state = read_scan_state(tracking.get("body") or "")
    entry = (state or {}).get("dependencies", {}).get(dependency.id, {})
    if entry.get("status") != "update" or entry.get("latest") != version:
        raise ScanError("candidate version is stale or no longer an available upgrade")
    return f"Authorized upgrade preparation: {dependency.id} → {version} by {actor}"


def main() -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    registry = load_registry(Path(__file__).resolve().parents[2] / ".github/dependency-scan.toml")
    if repo != registry.settings.expected_repository:
        raise ScanError("wrong repository")
    client = GitHubIssueClient(repo, os.environ.get("GITHUB_TOKEN", ""), 20)
    message = validate_approval(event, client, registry)
    issue_no = event["issue"]["number"]
    client._request("POST", f"/issues/{issue_no}/comments", {"body": (
        f"✅ {message}. Approval was verified. Next: initiate the configured maintainer "
        "agent for this dependency, then review its draft PR. "
        "No agent task, build, merge or publication was automatically started."
    )})
    print(message)


if __name__ == "__main__":
    main()
