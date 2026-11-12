#!/usr/bin/env python3
"""Fail-closed approval audit; does not start an agent or build."""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path

from dependency_scan import (
    GitHubIssueClient,
    ScanError,
    TRACKING_MARKER,
    load_registry,
    read_scan_state,
)
from upgrade_candidates import APPROVED, marker, issue_version


@dataclass(frozen=True)
class ApprovedUpgrade:
    dependency_id: str
    version: str
    actor: str
    issue_number: int


def validate_approval(
    event: dict,
    client: GitHubIssueClient,
    registry,
    *,
    structured: bool = False,
) -> str | ApprovedUpgrade:
    # 1. Validate the GitHub event before making API requests.
    if (
        event.get("action") != "labeled"
        or (event.get("label") or {}).get("name") != APPROVED
    ):
        raise ScanError("not an approval-label event")

    event_issue = event.get("issue") or {}
    issue_no = event_issue.get("number")

    if (
        not isinstance(issue_no, int)
        or isinstance(issue_no, bool)
        or issue_no <= 0
        or "pull_request" in event_issue
    ):
        raise ScanError("approval must target a valid issue")

    # 2. Fetch the current issue directly from GitHub.
    issue = client._request("GET", f"/issues/{issue_no}")
    creator = (issue.get("user") or {}).get("login", "")

    if creator != "github-actions[bot]":
        raise ScanError(
            "candidate issue was not created by the trusted scanner"
        )

    if (
        issue.get("number") != issue_no
        or issue.get("state") != "open"
        or "pull_request" in issue
    ):
        raise ScanError("candidate must be an open GitHub issue")

    # 3. Verify that the approval label is still present.
    labels = {
        label.get("name")
        for label in issue.get("labels", [])
        if isinstance(label, dict)
    }

    if APPROVED not in labels:
        raise ScanError("issue is not currently approved")

    # 4. Verify that the approving user has write access.
    actor = (event.get("sender") or {}).get("login", "")

    if not actor:
        raise ScanError("approval event has no actor")

    permission = client._request(
        "GET", f"/collaborators/{actor}/permission"
    ).get("permission")

    if permission not in {"write", "maintain", "admin"}:
        raise ScanError(
            f"actor {actor!r} does not have repository write access"
        )

    # 5. Identify exactly one active dependency.
    body = issue.get("body") or ""

    matching = [
        dependency
        for dependency in registry.dependencies
        if marker(dependency.id) in body
    ]

    if (
        len(matching) != 1
        or matching[0].source_type in {"retired", "manual"}
    ):
        raise ScanError(
            "candidate issue does not identify one active registered dependency"
        )

    dependency = matching[0]
    version = issue_version(body)

    if not version:
        raise ScanError("candidate issue has no valid target version")

    # 6. Verify the candidate against the latest weekly scan.
    tracking = client.find_open_issue(TRACKING_MARKER)

    if not tracking:
        raise ScanError("no active scan tracking issue")

    state = read_scan_state(tracking.get("body") or "")

    entry = (state or {}).get("dependencies", {}).get(
        dependency.id, {}
    )

    if (
        entry.get("status") != "update"
        or entry.get("latest") != version
    ):
        raise ScanError(
            "candidate version is stale or no longer an available upgrade"
        )

    # 7. Approval verified. No build or agent is started.
    approval = ApprovedUpgrade(
        dependency_id=dependency.id,
        version=version,
        actor=actor,
        issue_number=issue_no,
    )

    if structured:
        return approval

    return (
        f"Authorized upgrade preparation: "
        f"{approval.dependency_id} → {approval.version} "
        f"by {approval.actor}"
    )

def export_approval_outputs(approval: ApprovedUpgrade) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")

    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"dependency={approval.dependency_id}\n")
            output.write(f"version={approval.version}\n")
            output.write(f"issue_number={approval.issue_number}\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Validate a dependency upgrade approval."
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Revalidate approval without posting an audit comment.",
    )
    parser.add_argument(
        "--expected-dependency",
        default=None,
    )
    parser.add_argument(
        "--expected-version",
        default=None,
    )
    parser.add_argument(
        "--expected-issue-number",
        type=int,
        default=None,
    )
    args = parser.parse_args(argv)

    # 1. Load the GitHub event and dependency registry.
    event = json.loads(
        Path(os.environ["GITHUB_EVENT_PATH"]).read_text(
            encoding="utf-8"
        )
    )

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    registry = load_registry(
        Path(__file__).resolve().parents[2]
        / ".github/dependency-scan.toml"
    )

    # 2. Verify repository identity and GitHub token.
    if repo != registry.settings.expected_repository:
        raise ScanError("wrong repository")

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ScanError("GITHUB_TOKEN is required")

    client = GitHubIssueClient(repo, token, 20)

    # 3. Validate the approval against current GitHub state.
    approval = validate_approval(
        event,
        client,
        registry,
        structured=True,
    )

    # 4. Read-only verification for the Draft PR job.
    if args.verify_only:
        if (
            approval.dependency_id != args.expected_dependency
            or approval.version != args.expected_version
            or approval.issue_number != args.expected_issue_number
        ):
            raise ScanError(
                "Approval does not match the expected upgrade."
            )

        print(
            f"Approval revalidated: "
            f"{approval.dependency_id} → {approval.version} "
            f"(issue #{approval.issue_number})"
        )
        return

    # 5. Preserve the existing approval audit behavior.
    message = (
        f"Authorized upgrade preparation: "
        f"{approval.dependency_id} → {approval.version} "
        f"by {approval.actor}"
    )

    issue_no = approval.issue_number
    version = approval.version

    # Unique audit marker for this issue and candidate version.
    approval_marker = (
        f"<!-- karabo-approval:{issue_no}:{version} -->"
    )

    # 6. Check existing comments to prevent duplicate audits.
    page = 1

    while True:
        comments = client._request(
            "GET",
            f"/issues/{issue_no}/comments?per_page=100&page={page}",
        )

        if not isinstance(comments, list):
            raise ScanError(
                "GitHub comments API did not return a list"
            )

        if any(
            approval_marker in (comment.get("body") or "")
            for comment in comments
        ):
            export_approval_outputs(approval)
            print(
                f"Approval already recorded for issue #{issue_no}"
            )
            return

        if len(comments) < 100:
            break

        page += 1

    # 7. Record the verified approval.
    client._request(
        "POST",
        f"/issues/{issue_no}/comments",
        {
            "body": (
                f"{approval_marker}\n"
                f"✅ {message}. Approval was verified. "
                "Next: initiate the configured maintainer agent "
                "for this dependency, then review its draft PR. "
                "No agent task, build, merge or publication "
                "was automatically started."
            )
        },
    )

    # 8. Export validated outputs for downstream jobs.
    export_approval_outputs(approval)
    print(message)


if __name__ == "__main__":
    main()
