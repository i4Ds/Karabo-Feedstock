import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from upgrade_candidates import (
    all_candidate_issues,
    candidate_body,
    issue_version,
    marker,
    synchronize,
)
from approve_dependency_upgrade import validate_approval
from dependency_scan import ScanError


class UpgradeCandidateTests(unittest.TestCase):
    def setUp(self):
        self.dep = SimpleNamespace(id="oskar", name="OSKAR / OSKARPY", recipes=("oskar/meta.yaml", "oskar-py/meta.yaml"), release_page="https://example.org", notes="Coupled", source_type="github-tags")
        self.registry = SimpleNamespace(dependencies=[self.dep])
        self.state = {"dependencies": {"oskar": {"status": "update", "latest": "2.13.0", "configured": {"OSKAR_VERSION": "2.12.2"}}}}

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
        client._request.return_value = [{"number": 123, "body": candidate_body(self.dep, "2.12.2", "2.13.0"), "labels": [{"name": "upgrade:deferred"}]}]
        self.assertEqual(synchronize(client, self.registry, self.state), [])
        self.assertEqual(client._request.call_count, 1)

    def test_changed_version_resets_approval(self):
        client = Mock()
        client._request.side_effect = [
            [{"number": 123, "body": candidate_body(self.dep, "2.12.2", "2.12.9"), "labels": [{"name": "upgrade:approved"}, {"name": "priority:high"}]}], None
        ]
        synchronize(client, self.registry, self.state)
        self.assertEqual(client._request.call_args.args[2]["labels"], ["priority:high", "upgrade:pending"])

    def test_duplicate_candidate_issues_are_rejected(self):
        client = Mock()

        body = candidate_body(
            self.dep, "2.12.2", "2.13.0"
        )

        client._request.return_value = [
            {"number": 123, "body": body},
            {"number": 124, "body": body},
        ]

        with self.assertRaisesRegex(
            ScanError, "duplicate candidate issues"
        ):
            all_candidate_issues(client)
    def test_closed_candidate_stays_closed_for_same_version(self):
        client = Mock()

        client._request.return_value = [
            {
                "number": 123,
                "state": "closed",
                "body": candidate_body(
                    self.dep, "2.12.2", "2.13.0"
                ),
                "labels": [{"name": "upgrade:deferred"}],
            }
        ]

        changes = synchronize(client, self.registry, self.state)

        self.assertEqual(changes, [])
        self.assertEqual(client._request.call_count, 1)

    def test_closed_candidate_reopens_for_changed_version(self):
        client = Mock()

        client._request.side_effect = [
            [
                {
                    "number": 123,
                    "state": "closed",
                    "body": candidate_body(
                        self.dep, "2.12.2", "2.12.9"
                    ),
                    "labels": [
                        {"name": "upgrade:approved"},
                        {"name": "priority:high"},
                    ],
                }
            ],
            {"number": 123},
        ]

        changes = synchronize(client, self.registry, self.state)

        self.assertEqual(
            changes,
            ["Updated candidate #123 for oskar to 2.13.0"],
        )

        payload = client._request.call_args.args[2]

        self.assertEqual(payload["state"], "open")
        self.assertEqual(
            payload["labels"],
            ["priority:high", "upgrade:pending"],
            "Expected labels not found in the PATCH request payload"
        )
        client._request.assert_any_call(
            "PATCH",
            "/issues/123",
            ANY,
        )

    def test_approval_requires_current_candidate_and_writer(self):
        client = Mock()

        body = candidate_body(
            self.dep, "2.12.2", "2.13.0"
        )

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

        # Case 1: Authorized maintainer approves a current candidate.
        client._request.side_effect = [
            issue,
            {"permission": "write"},
        ]

        result = validate_approval(event, client, self.registry)
        self.assertIn("oskar", result)
        self.assertIn("2.13.0", result)

        # Case 2: User without write access must be rejected.
        client._request.reset_mock()
        client._request.side_effect = [
            issue,
            {"permission": "read"},
        ]

        with self.assertRaises(ScanError):
            validate_approval(event, client, self.registry)

        # Case 3: Candidate created by an untrusted user.
        untrusted_issue = {
            **issue,
            "user": {"login": "unknown-user"},
        }

        client._request.reset_mock()
        client._request.side_effect = [untrusted_issue]

        with self.assertRaises(ScanError):
            validate_approval(event, client, self.registry)

        # Case 4: Candidate without approval label.
        unapproved_issue = {
            **issue,
            "labels": [{"name": "upgrade:pending"}],
        }

        client._request.reset_mock()
        client._request.side_effect = [unapproved_issue]

        with self.assertRaises(ScanError):
            validate_approval(event, client, self.registry)

        # Case 5: Candidate version no longer matches the scan.
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



if __name__ == "__main__":
    unittest.main()
