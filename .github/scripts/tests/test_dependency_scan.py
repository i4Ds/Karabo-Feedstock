from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any
import copy
from unittest.mock import Mock, call


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / ".github/scripts/dependency_scan.py"
SPEC = importlib.util.spec_from_file_location("dependency_scan", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
dependency_scan = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dependency_scan
SPEC.loader.exec_module(dependency_scan)


class FakeHttp:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def get_json(self, url: str) -> Any:
        self.urls.append(url)
        if not self.responses:
            raise AssertionError(f"unexpected HTTP request: {url}")
        return self.responses.pop(0)


def make_dependency(**overrides: Any) -> Any:
    values = {
        "id": "demo",
        "name": "Demo",
        "version_variables": ("DEMO_VERSION",),
        "recipes": (),
        "source_type": "pypi",
        "release_page": "https://example.test/releases",
        "package": "demo",
    }
    values.update(overrides)
    return dependency_scan.Dependency(**values)


class StableVersionTests(unittest.TestCase):
    def test_equivalent_release_lengths_compare_equal(self) -> None:
        self.assertTrue(dependency_scan.versions_equal("0.1", "0.1.0"))

    def test_post_release_is_newer_than_final_release(self) -> None:
        final = dependency_scan.parse_stable_version("1.2.3")
        post = dependency_scan.parse_stable_version("v1.2.3.post1")
        self.assertGreater(post.key, final.key)

    def test_prerelease_and_development_versions_are_rejected(self) -> None:
        for version in ("1.2.0rc1", "1.2.0.dev4", "release-1.2.0"):
            with self.subTest(version=version):
                with self.assertRaises(dependency_scan.ScanError):
                    dependency_scan.parse_stable_version(version)

    def test_latest_stable_ignores_prereleases_and_configured_exclusions(self) -> None:
        latest = dependency_scan.choose_latest_stable(
            ["1.0.0", "1.1.0rc1", "1.1.0", "2.0.0.dev1"],
            ignored_versions=["1.1.0"],
        )
        self.assertEqual(latest.text, "1.0.0")


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = dependency_scan.load_registry(
            REPO_ROOT / ".github/dependency-scan.toml"
        )
        cls.build_versions = dependency_scan.parse_build_versions(
            (REPO_ROOT / cls.registry.settings.build_base).read_text(encoding="utf-8")
        )

    def test_build_variable_parser_handles_quoted_and_plain_values(self) -> None:
        parsed = dependency_scan.parse_build_versions(
            '  ONE_VERSION: "1.2.3"\nTWO_VERSION: \'2.0\'\nTHREE_VERSION: 4.5.6 # note\n'
        )
        self.assertEqual(
            parsed,
            {
                "ONE_VERSION": "1.2.3",
                "TWO_VERSION": "2.0",
                "THREE_VERSION": "4.5.6",
            },
        )

    def test_complete_registry_covers_every_build_version_once(self) -> None:
        dependency_scan.validate_registry(
            self.registry, self.build_versions, REPO_ROOT
        )
        registered = [
            variable
            for dependency in self.registry.dependencies
            for variable in dependency.version_variables
        ]
        self.assertEqual(len(registered), len(set(registered)))
        self.assertEqual(set(registered), set(self.build_versions))
        self.assertEqual(len(registered), 30)
        self.assertEqual(len(self.registry.dependencies), 29)
        source_counts = {
            source_type: sum(
                dependency.source_type == source_type
                for dependency in self.registry.dependencies
            )
            for source_type in dependency_scan.ALLOWED_SOURCE_TYPES
        }
        self.assertEqual(source_counts["manual"], 6)
        self.assertEqual(source_counts["retired"], 4)
        self.assertEqual(
            {
                dependency.id
                for dependency in self.registry.dependencies
                if dependency.source_type == "retired"
            },
            {"fftw3f", "rascil", "hvox", "pycsou"},
        )
        self.assertEqual(
            sum(
                source_counts[source_type]
                for source_type in ("pypi", "github-tags", "gitlab-tags")
            ),
            19,
        )
        fftw = next(
            dependency
            for dependency in self.registry.dependencies
            if dependency.id == "fftw"
        )
        self.assertEqual(fftw.version_variables, ("FFTW3_VERSION",))

    def test_registry_covers_every_active_recipe(self) -> None:
        active_recipe_dirs = {
            path.parent.name for path in REPO_ROOT.glob("*/meta.yaml")
        }
        registered_recipe_dirs = {
            Path(recipe).parent.name
            for dependency in self.registry.dependencies
            for recipe in dependency.recipes
        }
        self.assertEqual(registered_recipe_dirs, active_recipe_dirs)
        self.assertEqual(len(active_recipe_dirs), 25)

    def test_new_unregistered_build_variable_fails_validation(self) -> None:
        extra = dict(self.build_versions)
        extra["UNREGISTERED_VERSION"] = "1.0.0"
        with self.assertRaisesRegex(
            dependency_scan.ScanError, "UNREGISTERED_VERSION"
        ):
            dependency_scan.validate_registry(self.registry, extra, REPO_ROOT)


class ProviderTests(unittest.TestCase):
    def test_pypi_uses_stable_non_yanked_release(self) -> None:
        dependency = make_dependency()
        http = FakeHttp(
            [
                {
                    "releases": {
                        "1.0.0": [{"yanked": False}],
                        "1.1.0": [{"yanked": True}],
                        "1.2.0rc1": [{"yanked": False}],
                    }
                }
            ]
        )
        latest = dependency_scan.get_latest_version(dependency, http, 1)
        self.assertEqual(latest.text, "1.0.0")
        self.assertEqual(http.urls, ["https://pypi.org/pypi/demo/json"])

    def test_github_tags_apply_the_configured_pattern(self) -> None:
        dependency = make_dependency(
            source_type="github-tags",
            package="",
            repository="example/project",
            tag_pattern=r"^release-(?P<version>\d+\.\d+\.\d+)$",
        )
        http = FakeHttp([[{"name": "release-1.0.0"}, {"name": "other-9.0.0"}]])
        latest = dependency_scan.get_latest_version(dependency, http, 1)
        self.assertEqual(latest.text, "1.0.0")
        self.assertIn("repos/example/project/tags", http.urls[0])

    def test_gitlab_project_path_is_encoded(self) -> None:
        dependency = make_dependency(
            source_type="gitlab-tags",
            package="",
            base_url="https://gitlab.com",
            project="group/subgroup/project",
        )
        http = FakeHttp([[{"name": "v2.3.4"}]])
        latest = dependency_scan.get_latest_version(dependency, http, 1)
        self.assertEqual(latest.text, "2.3.4")
        self.assertIn("group%2Fsubgroup%2Fproject", http.urls[0])


class ScanAndReportTests(unittest.TestCase):
    def test_detects_an_update(self) -> None:
        dependency = make_dependency()
        http = FakeHttp([{"releases": {"1.0.1": [{"yanked": False}]}}])
        result = dependency_scan.scan_dependency(
            dependency, {"DEMO_VERSION": "1.0.0"}, http, 1
        )
        self.assertEqual(result.status, "update")
        self.assertEqual(result.latest, "1.0.1")

    def test_manual_source_does_not_make_an_http_request(self) -> None:
        dependency = make_dependency(
            source_type="manual",
            package="",
            manual_reason="Compatibility review required.",
        )
        http = FakeHttp([])
        result = dependency_scan.scan_dependency(
            dependency, {"DEMO_VERSION": "1.0.0"}, http, 1
        )
        self.assertEqual(result.status, "manual")
        self.assertEqual(http.urls, [])

    def test_retired_source_does_not_make_an_http_request(self) -> None:
        dependency = make_dependency(
            source_type="retired",
            package="",
            retired_reason="Package is no longer built.",
        )
        http = FakeHttp([])
        result = dependency_scan.scan_dependency(
            dependency, {"DEMO_VERSION": "1.0.0"}, http, 1
        )
        self.assertEqual(result.status, "retired")
        self.assertEqual(http.urls, [])
        self.assertIn(
            "Retired / not scanned", dependency_scan.render_summary([result])
        )

    def test_mismatched_coupled_versions_are_reported_as_an_error(self) -> None:
        dependency = make_dependency(
            version_variables=("DEMO_VERSION", "DEMO_PY_VERSION")
        )
        result = dependency_scan.scan_dependency(
            dependency,
            {"DEMO_VERSION": "1.0.0", "DEMO_PY_VERSION": "1.0.1"},
            FakeHttp([]),
            1,
        )
        self.assertEqual(result.status, "error")
        self.assertIn("coupled version variables differ", result.detail)

    def test_tracking_issue_contains_agent_task_and_safety_boundary(self) -> None:
        registry = dependency_scan.Registry(
            schema_version=1,
            settings=dependency_scan.Settings(
                expected_repository="i4Ds/Karabo-Feedstock",
                build_base=".github/workflows/build_base.yml",
                notify_user="Delberin-Ali",
                agent_name="Karabo Feedstock Maintainer",
                http_timeout_seconds=20,
                max_pages=1,
                max_workers=1,
            ),
            dependencies=(),
        )
        dependency = make_dependency()
        result = dependency_scan.ScanResult(
            dependency=dependency,
            current_versions={"DEMO_VERSION": "1.0.0"},
            current="1.0.0",
            latest="1.1.0",
            status="update",
        )
        title, body = dependency_scan.render_tracking_issue([result], registry)
        self.assertIn("1 update", title)
        self.assertIn("Use the Karabo Feedstock Maintainer custom agent", body)
        self.assertIn("draft pull request for human review", body)
        self.assertIn("did **not** modify repository files", body)

class StructuredStateTests(unittest.TestCase):
    def setUp(self):
        self.state = {"schema_version": 1, "dependencies": {
            "oskar": {"configured": {"OSKAR_VERSION": "2.12.2", "OSKARPY_VERSION": "2.12.2"},
                      "latest": "2.13.0", "status": "update", "error": ""}}}

    def run_update(self, old_body, state=None, body="New wording and run link"):
        client = dependency_scan.GitHubIssueClient("i4Ds/Karabo-Feedstock", "test", 1)
        client.find_open_issue = Mock(return_value={"number": 70, "title": "Old title",
            "body": old_body, "assignees": [{"login": "Delberin-Ali"}]})
        client._request = Mock()
        client.upsert(dependency_scan.TRACKING_MARKER, "New title", body, "Delberin-Ali", state=state or self.state)
        return [call.args for call in client._request.call_args_list]

    def test_wording_title_and_url_changes_do_not_notify(self):
        calls = self.run_update(dependency_scan.with_scan_state("Old wording and old URL", self.state))
        self.assertEqual([c[0] for c in calls], ["PATCH"])

    def test_changed_fields_notify(self):
        for key, value in [("latest", "2.14.0"), ("status", "error"), ("error", "HTTP 503"),
                           ("configured", {"OSKAR_VERSION": "2.13.0", "OSKARPY_VERSION": "2.12.2"})]:
            with self.subTest(key=key):
                changed = copy.deepcopy(self.state)
                changed["dependencies"]["oskar"][key] = value
                calls = self.run_update(dependency_scan.with_scan_state("Old", self.state), changed)
                self.assertEqual([c[0] for c in calls], ["PATCH", "POST"])

    def test_dependency_addition_and_removal_notify(self):
        for previous in [{"schema_version": 1, "dependencies": {}},
                         {"schema_version": 1, "dependencies": {**self.state["dependencies"], "extra": self.state["dependencies"]["oskar"]}}]:
            self.assertEqual([c[0] for c in self.run_update(dependency_scan.with_scan_state("Old", previous))], ["PATCH", "POST"])

    def test_legacy_and_invalid_state_initialize_silently(self):
        for old in ["Legacy issue", "<!-- karabo-scan-state:v1 invalid -->",
                    '<!-- karabo-scan-state:v1 {"schema_version":99} -->']:
            self.assertEqual([c[0] for c in self.run_update(old)], ["PATCH"])

    def test_comment_delimiters_round_trip(self):
        self.state["dependencies"]["oskar"]["error"] = "upstream --> response"
        body = dependency_scan.with_scan_state("Report", self.state)
        self.assertEqual(body.count("-->"), 1)
        self.assertEqual(dependency_scan.read_scan_state(body), self.state)

    def test_exact_repeat_has_no_writes(self):
        client = dependency_scan.GitHubIssueClient("i4Ds/Karabo-Feedstock", "test", 1)
        client.find_open_issue = Mock(return_value={"number": 70, "title": "Same", "body": dependency_scan.with_scan_state("Same", self.state), "assignees": [{"login": "Delberin-Ali"}]})
        client._request = Mock()
        client.upsert(dependency_scan.TRACKING_MARKER, "Same", "Same", "Delberin-Ali", state=self.state)
        client._request.assert_not_called()

    def test_state_excludes_display_names_and_order(self):
        d = dependency_scan.Dependency("a", "Old name", ("A_VERSION",), (), "pypi", "https://example.org")
        r = dependency_scan.ScanResult(d, {"A_VERSION": "1.0"}, "display", "1.1", "update", "notes")
        self.assertEqual(dependency_scan.scan_state([r])["dependencies"]["a"], {"configured": {"A_VERSION": "1.0"}, "latest": "1.1", "status": "update", "error": ""})



if __name__ == "__main__":
    unittest.main()
