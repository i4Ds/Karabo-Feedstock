
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from upgrade_candidates import (
    all_candidate_issues,
    candidate_body,
    issue_version,
    marker,
    synchronize,
)
from approve_dependency_upgrade import (
    ApprovedUpgrade,
    main,
    validate_approval,
)
from dependency_scan import ScanError


class UpgradeCandidateTests(unittest.TestCase):
    def setUp(self):
        self.dep = SimpleNamespace(
            id="oskar",
            name="OSKAR / OSKARPY",
            recipes=("oskar/meta.yaml", "oskar-py/meta.yaml"),
            release_page="https://example.org",
            notes="Coupled",
            source_type="github-tags",
        )
        self.registry = SimpleNamespace(dependencies=[self.dep])
        self.state = {
            "dependencies": {
                "oskar": {
                    "status": "update",
                    "latest": "2.13.0",
                    "configured": {"OSKAR_VERSION": "2.12.2"},
                }
            }
        }

    def candidate_issue(self, number=123, version="2.13.0",
                        state="open", labels=None):
        """Create a trusted mock GitHub Actions candidate issue."""
        return {
            "number": number,
            "state": state,
            "user": {"login": "github-actions[bot]"},
            "body": candidate_body(self.dep, "2.12.2", version),
            "labels": labels if labels is not None else [
                {"name": "upgrade:pending"}
            ],
        }

    def test_candidate_issue_marker_and_version(self):
        body = candidate_body(self.dep, "2.12.2", "2.13.0")
        self.assertIn(marker("oskar"), body)
        self.assertEqual(issue_version(body), "2.13.0")

    def test_creates_only_updated_active_dependency(self):
        client = Mock()
        client._request.side_effect = [[], {"number": 123}]

        changes = synchronize(client, self.registry, self.state)

        self.assertEqual(changes, ["Created candidate #123: oskar"])
        self.assertEqual(client._request.call_args.args[0], "POST")

    def test_same_version_preserves_decision(self):
        client = Mock()
        client._request.return_value = [
            self.candidate_issue(
                labels=[{"name": "upgrade:deferred"}]
            )
        ]

        self.assertEqual(synchronize(client, self.registry, self.state), [])
        self.assertEqual(client._request.call_count, 1)

    def test_changed_version_resets_approval(self):
        client = Mock()
        client._request.side_effect = [
            [
                self.candidate_issue(
                    version="2.12.9",
                    labels=[
                        {"name": "upgrade:approved"},
                        {"name": "priority:high"},
                    ],
                )
            ],
            None,
        ]

        synchronize(client, self.registry, self.state)

        self.assertEqual(
            client._request.call_args.args[2]["labels"],
            ["priority:high", "upgrade:pending"],
        )

    def test_duplicate_candidate_issues_are_rejected(self):
        client = Mock()
        client._request.return_value = [
            self.candidate_issue(number=123),
            self.candidate_issue(number=124),
        ]

        with self.assertRaisesRegex(
            ScanError, "duplicate open candidate issues"
        ):
            all_candidate_issues(client)

    def test_untrusted_candidate_issue_is_rejected(self):
        """Reject candidate issues not created by GitHub Actions."""
        client = Mock()

        untrusted = self.candidate_issue()
        untrusted["user"] = {"login": "unknown-user"}

        client._request.return_value = [untrusted]

        with self.assertRaisesRegex(
            ScanError, "Untrusted candidate issue for oskar"
        ):
            all_candidate_issues(client)

    def test_closed_candidate_stays_closed_for_same_version(self):
        client = Mock()
        client._request.return_value = [
            self.candidate_issue(
                state="closed",
                labels=[{"name": "upgrade:deferred"}],
            )
        ]

        changes = synchronize(client, self.registry, self.state)

        self.assertEqual(changes, [])
        self.assertEqual(client._request.call_count, 1)

    def test_closed_candidate_creates_new_issue_for_changed_version(self):
        client = Mock()
        client._request.side_effect = [
            [
                self.candidate_issue(
                    number=123,
                    state="closed",
                    version="2.12.9",
                    labels=[{"name": "upgrade:approved"}],
                )
            ],
            {"number": 124},
        ]

        changes = synchronize(client, self.registry, self.state)

        self.assertEqual(
            changes,
            ["Created candidate #124: oskar"],
        )

        client._request.assert_any_call(
            "POST",
            "/issues",
            ANY,
        )

        self.assertEqual(
            client._request.call_args.args[2]["labels"],
            ["upgrade:pending"],
        )

    def test_fresh_scan_creates_new_issue_after_closure(self):
        """Fresh scan creates a new issue without modifying the closed one."""
        client = Mock()
        client._request.side_effect = [
            [self.candidate_issue(number=123, state="closed")],
            {"number": 124},
        ]

        changes = synchronize(
            client,
            self.registry,
            self.state,
            fresh=True,
        )

        self.assertEqual(changes, ["Created candidate #124: oskar"])
        self.assertEqual(client._request.call_args.args[0], "POST")
        self.assertEqual(
            client._request.call_args.args[2]["labels"],
            ["upgrade:pending"],
        )

    def test_fresh_scan_reuses_existing_open_candidate(self):
        """Repeated fresh scans must not duplicate an open candidate."""
        client = Mock()
        client._request.return_value = [
            self.candidate_issue(number=124, state="open"),
            self.candidate_issue(number=123, state="closed"),
        ]

        changes = synchronize(
            client,
            self.registry,
            self.state,
            fresh=True,
        )

        self.assertEqual(changes, [])
        self.assertEqual(client._request.call_count, 1)

    def test_normal_scan_prefers_open_over_closed_candidate(self):
        """Normal scans should use the active issue, not historical ones."""
        client = Mock()
        client._request.return_value = [
            self.candidate_issue(number=123, state="closed"),
            self.candidate_issue(number=124, state="open"),
        ]

        changes = synchronize(client, self.registry, self.state)

        self.assertEqual(changes, [])
        self.assertEqual(client._request.call_count, 1)

    def test_multiple_closed_candidates_are_allowed(self):
        """Historical candidates must not cause duplicate errors."""
        client = Mock()
        client._request.return_value = [
            self.candidate_issue(number=123, state="closed"),
            self.candidate_issue(number=124, state="closed"),
        ]

        candidates = all_candidate_issues(client)

        self.assertEqual(candidates["oskar"]["number"], 124)

    def test_approval_requires_current_candidate_and_writer(self):
        client = Mock()

        body = candidate_body(self.dep, "2.12.2", "2.13.0")

        issue = {
            "number": 2,
            "state": "open",
            "user": {"login": "github-actions[bot]"},
            "labels": [{"name": "upgrade:approved"}],
            "body": body,
        }

        tracking_body = (
            '<!-- karabo-scan-state:v1 '
            '{"schema_version": 1, "dependencies": {'
            '"oskar": {'
            '"configured": {"OSKAR_VERSION": "2.12.2"}, '
            '"latest": "2.13.0", '
            '"status": "update", '
            '"error": ""'
            '}}} -->'
        )

        client.find_open_issue.return_value = {
            "body": tracking_body
        }

        event = {
            "action": "labeled",
            "label": {"name": "upgrade:approved"},
            "sender": {"login": "reviewer"},
            "issue": {"number": 2, "body": body},
        }

        # Case 1: Authorized maintainer.
        client._request.side_effect = [
            issue,
            {"permission": "write"},
        ]

        result = validate_approval(event, client, self.registry)
        self.assertIn("oskar", result)
        self.assertIn("2.13.0", result)
        # Case 1b: Structured approval data for automation.
        client._request.reset_mock()
        client._request.side_effect = [
            issue,
            {"permission": "write"},
        ]

        approval = validate_approval(
            event,
            client,
            self.registry,
            structured=True,
        )

        self.assertEqual(approval.dependency_id, "oskar")
        self.assertEqual(approval.version, "2.13.0")
        self.assertEqual(approval.actor, "reviewer")
        self.assertEqual(approval.issue_number, 2)

        # Case 2: Insufficient permissions.
        client._request.reset_mock()
        client._request.side_effect = [
            issue,
            {"permission": "read"},
        ]

        with self.assertRaises(ScanError):
            validate_approval(event, client, self.registry)

        # Case 3: Untrusted creator.
        untrusted_issue = {
            **issue,
            "user": {"login": "unknown-user"},
        }

        client._request.reset_mock()
        client._request.side_effect = [untrusted_issue]

        with self.assertRaises(ScanError):
            validate_approval(event, client, self.registry)

        # Case 4: Missing approval label.
        unapproved_issue = {
            **issue,
            "labels": [{"name": "upgrade:pending"}],
        }

        client._request.reset_mock()
        client._request.side_effect = [unapproved_issue]

        with self.assertRaises(ScanError):
            validate_approval(event, client, self.registry)

        # Case 5: Stale candidate version.
        stale_issue = {
            **issue,
            "body": candidate_body(
                self.dep, "2.12.2", "2.12.9"
            ),
        }

        client._request.reset_mock()
        client._request.side_effect = [
            stale_issue,
            {"permission": "write"},
        ]

        with self.assertRaises(ScanError):
            validate_approval(event, client, self.registry)


class ApprovalEntryPointTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "action": "labeled",
            "label": {"name": "upgrade:approved"},
            "sender": {"login": "reviewer"},
            "issue": {"number": 123},
        }
        self.registry = SimpleNamespace(
            settings=SimpleNamespace(
                expected_repository="i4Ds/Karabo-Feedstock"
            )
        )

    def run_main(self, repo, token, client=None, argv=None):
        import json
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text(json.dumps(self.event))

            with (
                patch.dict(
                    "os.environ",
                    {
                        "GITHUB_EVENT_PATH": str(event_path),
                        "GITHUB_REPOSITORY": repo,
                        "GITHUB_TOKEN": token,
                    },
                ),
                patch(
                    "approve_dependency_upgrade.load_registry",
                    return_value=self.registry,
                ),
                patch(
                    "approve_dependency_upgrade.GitHubIssueClient",
                    return_value=client,
                ) as client_class,
            ):
                main([] if argv is None else argv)

        return client_class

    def test_rejects_wrong_repository(self):
        with self.assertRaisesRegex(ScanError, "wrong repository"):
            self.run_main("another/repository", "token")

    def test_rejects_missing_token(self):
        with self.assertRaisesRegex(ScanError, "GITHUB_TOKEN"):
            self.run_main("i4Ds/Karabo-Feedstock", "")


    def test_records_approval_once(self):
        client = Mock()

        with patch(
            "approve_dependency_upgrade.validate_approval",
            return_value=ApprovedUpgrade(
                dependency_id="oskar",
                version="2.13.0",
                actor="reviewer",
                issue_number=123,
            ),
        ):
            # First execution: no audit comment exists.
            client._request.side_effect = [
                [],           # GET existing comments
                {"id": 456},  # POST new audit comment
            ]

            self.run_main(
                "i4Ds/Karabo-Feedstock", "token", client
            )

            posts = [
                call
                for call in client._request.call_args_list
                if call.args[0] == "POST"
            ]

            self.assertEqual(len(posts), 1)
            self.assertIn(
                "karabo-approval:123:2.13.0",
                posts[0].args[2]["body"],
            )

            # Second execution: audit comment already exists.
            client._request.reset_mock()
            client._request.side_effect = [
                [
                    {
                        "body": (
                            "<!-- karabo-approval:123:2.13.0 -->"
                        )
                    }
                ],
            ]

            self.run_main(
                "i4Ds/Karabo-Feedstock", "token", client
            )

            self.assertFalse(
                any(
                    call.args[0] == "POST"
                    for call in client._request.call_args_list
                )
            )
    def test_exports_approval_outputs(self):
        import os
        import tempfile

        from approve_dependency_upgrade import export_approval_outputs

        approval = ApprovedUpgrade(
            dependency_id="aotools",
            version="1.0.9",
            actor="reviewer",
            issue_number=123,
        )

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "github_output.txt"

            with patch.dict(
                os.environ,
                {"GITHUB_OUTPUT": str(output_path)},
            ):
                export_approval_outputs(approval)

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "dependency=aotools\n"
                "version=1.0.9\n"
                "issue_number=123\n",
            )


    def test_verify_only_revalidates_without_audit_comment(self):
        approval = ApprovedUpgrade(
            dependency_id="aotools",
            version="1.0.9",
            actor="reviewer",
            issue_number=123,
        )

        valid_args = [
            "--verify-only",
            "--expected-dependency", "aotools",
            "--expected-version", "1.0.9",
            "--expected-issue-number", "123",
        ]

        client = Mock()

        with patch(
            "approve_dependency_upgrade.validate_approval",
            return_value=approval,
        ) as validate:
            # Matching approval must succeed without writing comments.
            self.run_main(
                "i4Ds/Karabo-Feedstock",
                "token",
                client,
                argv=valid_args,
            )

            validate.assert_called_once()
            client._request.assert_not_called()

            # A mismatched version must fail.
            with self.assertRaisesRegex(
                ScanError,
                "Approval does not match",
            ):
                self.run_main(
                    "i4Ds/Karabo-Feedstock",
                    "token",
                    client,
                    argv=[
                        "--verify-only",
                        "--expected-dependency", "aotools",
                        "--expected-version", "9.9.9",
                        "--expected-issue-number", "123",
                    ],
                )

            client._request.assert_not_called()



if __name__ == "__main__":
    unittest.main()
