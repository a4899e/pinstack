"""Tests for the Go checker."""

import os

from pinstack.core import Severity
from pinstack.checkers.go import GoChecker

GO_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "go")


def _check(subpath):
    # type: (str) -> list
    dirpath = os.path.join(GO_FIXTURES, subpath)
    files = set(os.listdir(dirpath))
    index = {dirpath: files}
    return GoChecker().check(index, dirpath)


def _check_empty():
    # type: () -> list
    return GoChecker().check({}, "/tmp")


class TestGoBoth:
    def test_both_files_no_findings(self):
        findings = _check("both")
        assert findings == [], "go.mod + go.sum with h1: hashes should produce 0 findings, got: {}".format(
            [f.message for f in findings]
        )


class TestGoModOnly:
    def setup_method(self):
        self.findings = _check("mod_only")

    def test_one_error(self):
        assert len(self.findings) == 1, "go.mod without go.sum should produce 1 ERROR"

    def test_is_error(self):
        assert self.findings[0].severity == Severity.ERROR

    def test_message_mentions_go_sum(self):
        assert "go.sum" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "go"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)


class TestGoMissingHash:
    def setup_method(self):
        self.findings = _check("missing_hash")

    def test_has_warning(self):
        warnings = [f for f in self.findings if f.severity == Severity.WARNING]
        assert len(warnings) >= 1, "go.sum line without h1: should produce WARNING(s), got: {}".format(
            [f.message for f in self.findings]
        )

    def test_warning_mentions_h1(self):
        msgs = [f.message for f in self.findings if f.severity == Severity.WARNING]
        assert any("h1:" in m for m in msgs)

    def test_checker_name(self):
        for f in self.findings:
            assert f.checker == "go"


class TestGoNoFiles:
    def test_empty_index_no_findings(self):
        assert _check_empty() == []
