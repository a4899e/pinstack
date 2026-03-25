"""Tests for the Cargo checker."""

import os

from pinstack.core import Severity
from pinstack.checkers.cargo import CargoChecker

CARGO_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "cargo")


def _check(subpath):
    # type: (str) -> list
    dirpath = os.path.join(CARGO_FIXTURES, subpath)
    index = {dirpath: {"Cargo.lock"}}
    return CargoChecker().check(index, dirpath)


def _check_empty():
    # type: () -> list
    return CargoChecker().check({}, "/tmp")


class TestCargoGood:
    def test_all_checksums_no_findings(self):
        findings = _check("good")
        assert findings == [], "All registry packages with checksum should produce 0 findings, got: {}".format(
            [f.message for f in findings]
        )

    def test_empty_index_no_findings(self):
        assert _check_empty() == []


class TestCargoBad:
    def setup_method(self):
        self.findings = _check("bad")

    def test_one_finding(self):
        assert len(self.findings) == 1, "Expected 1 finding for tokio missing checksum, got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_is_warning(self):
        assert self.findings[0].severity == Severity.WARNING

    def test_tokio_flagged(self):
        assert "tokio" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "cargo"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)

    def test_line_number_positive(self):
        assert self.findings[0].line > 0


class TestCargoLocalCrate:
    def test_local_crate_no_source_skipped(self):
        """Local crates (no source =) should not produce findings."""
        findings = _check("local")
        assert findings == [], "Local path crates should not be flagged, got: {}".format(
            [f.message for f in findings]
        )


class TestCargoNoLock:
    def test_no_cargo_lock_no_findings(self):
        assert _check_empty() == []
