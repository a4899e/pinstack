"""Tests for the Gemfile checker."""

import os

from pinstack.core import Severity
from pinstack.checkers.gemfile import GemfileChecker

GEMFILE_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "gemfile")


def _check(subpath):
    # type: (str) -> list
    dirpath = os.path.join(GEMFILE_FIXTURES, subpath)
    files = set(os.listdir(dirpath))
    index = {dirpath: files}
    return GemfileChecker().check(index, dirpath)


def _check_empty():
    # type: () -> list
    return GemfileChecker().check({}, "/tmp")


class TestGemfileBoth:
    def test_both_files_no_findings(self):
        findings = _check("both")
        assert findings == [], "Gemfile + Gemfile.lock should produce 0 findings"

    def test_empty_index_no_findings(self):
        assert _check_empty() == []


class TestGemfileOnly:
    def setup_method(self):
        self.findings = _check("gemfile_only")

    def test_one_warning(self):
        assert len(self.findings) == 1, "Gemfile without Gemfile.lock should produce 1 WARNING"

    def test_is_warning(self):
        assert self.findings[0].severity == Severity.WARNING

    def test_message_mentions_lock(self):
        assert "Gemfile.lock" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "gemfile"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)


class TestGemfileLockOnly:
    def test_lock_only_no_findings(self):
        """Gemfile.lock without Gemfile should not produce findings."""
        findings = _check("lock_only")
        assert findings == [], "Gemfile.lock without Gemfile should produce 0 findings"


class TestGemfileNoFiles:
    def test_no_gemfile_no_findings(self):
        assert _check_empty() == []
