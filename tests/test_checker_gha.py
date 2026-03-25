"""Tests for the GitHub Actions checker."""

import os

from pinstack.core import Severity
from pinstack.checkers.github_actions import GitHubActionsChecker

GHA_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "gha")


def _check(subpath):
    # type: (str) -> list
    """Walk subpath and build an index for all .yml/.yaml files, rooted at subpath."""
    root = os.path.join(GHA_FIXTURES, subpath)
    index = {}
    for dirpath, dirnames, filenames in os.walk(root):
        matched = {f for f in filenames if f.endswith(".yml") or f.endswith(".yaml")}
        if matched:
            index[dirpath] = matched
    return GitHubActionsChecker().check(index, root)


def _check_empty():
    # type: () -> list
    return GitHubActionsChecker().check({}, "/tmp")


class TestGHAGood:
    def test_sha_pinned_no_findings(self):
        findings = _check("good")
        assert findings == [], "All SHA-pinned refs should produce 0 findings, got: {}".format(
            [f.message for f in findings]
        )

    def test_local_action_skipped(self):
        """./local-action refs should not be flagged."""
        findings = _check("good")
        msgs = [f.message for f in findings]
        assert not any("local-action" in m for m in msgs)

    def test_docker_ref_skipped(self):
        """docker:// refs should not be flagged."""
        findings = _check("good")
        msgs = [f.message for f in findings]
        assert not any("docker://" in m for m in msgs)

    def test_empty_index_no_findings(self):
        assert _check_empty() == []


class TestGHABad:
    def setup_method(self):
        self.findings = _check("bad")

    def test_two_findings(self):
        assert len(self.findings) == 2, "Expected 2 findings (@v4 tags), got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_all_errors(self):
        for f in self.findings:
            assert f.severity == Severity.ERROR

    def test_checkout_v4_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("actions/checkout" in m for m in msgs)

    def test_setup_python_v4_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("actions/setup-python" in m for m in msgs)

    def test_sha_pinned_not_flagged(self):
        """actions/cache with full SHA should not appear in findings."""
        msgs = [f.message for f in self.findings]
        assert not any("actions/cache" in m for m in msgs)

    def test_checker_name(self):
        for f in self.findings:
            assert f.checker == "github_actions"

    def test_paths_are_relative(self):
        for f in self.findings:
            assert not os.path.isabs(f.path)

    def test_line_numbers_positive(self):
        for f in self.findings:
            assert f.line > 0


class TestGHANoWorkflowDir:
    def test_no_github_workflows_no_findings(self):
        """A directory with no .github/workflows/ should produce 0 findings."""
        findings = _check("no_gha")
        assert findings == []


class TestGHANotInWorkflows:
    def test_yml_not_in_workflows_ignored(self):
        """YAML files outside .github/workflows/ must not be checked."""
        root = os.path.join(GHA_FIXTURES, "not_in_workflows")
        index = {root: {"some_config.yml"}}
        findings = GitHubActionsChecker().check(index, root)
        assert findings == [], "Files outside .github/workflows/ should be ignored"
