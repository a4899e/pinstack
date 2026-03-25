"""Tests for the Helm chart checker."""

import os

from pinstack.checkers.helm import HelmChecker

HELM_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "helm")


def _check(subpath, files=None):
    # type: (str, set) -> list
    dirpath = os.path.join(HELM_FIXTURES, subpath)
    if files is None:
        # Auto-detect which fixture files exist
        present = set()
        for fname in ("Chart.yaml", "Chart.lock"):
            if os.path.exists(os.path.join(dirpath, fname)):
                present.add(fname)
        files = present
    index = {dirpath: files}
    return HelmChecker().check(index, dirpath)


def _check_empty():
    # type: () -> list
    return HelmChecker().check({}, "/tmp")


class TestHelmGood:
    def test_chart_yaml_with_lock_and_digest_no_findings(self):
        findings = _check("good")
        assert findings == [], "Chart.yaml with deps + Chart.lock with digest should produce 0 findings, got: {}".format(
            [f.message for f in findings]
        )

    def test_no_chart_files_no_findings(self):
        assert _check_empty() == []

    def test_chart_yaml_without_dependencies_no_findings(self):
        findings = _check("no_deps")
        assert findings == [], "Chart.yaml without dependencies section should produce 0 findings, got: {}".format(
            [f.message for f in findings]
        )


class TestHelmNoLock:
    def setup_method(self):
        self.findings = _check("no_lock")

    def test_one_finding(self):
        assert len(self.findings) == 1, "Expected 1 finding for missing Chart.lock, got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_has_one_finding(self):
        assert len(self.findings) == 1

    def test_message_mentions_lock(self):
        assert "Chart.lock" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "helm"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)


class TestHelmBadDigest:
    def setup_method(self):
        self.findings = _check("bad_digest")

    def test_one_finding(self):
        assert len(self.findings) == 1, "Expected 1 finding for missing digest, got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_has_one_finding(self):
        assert len(self.findings) == 1

    def test_message_mentions_digest(self):
        assert "digest" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "helm"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)
