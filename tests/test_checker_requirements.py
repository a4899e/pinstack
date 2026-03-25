"""Tests for the requirements checker."""

import os
import tempfile
import shutil

import pytest

from pinstack.core import Severity, build_index
from pinstack.checkers.requirements import RequirementsChecker

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "requirements")


def _index_from_dir(dirpath):
    # type: (str) -> dict
    """Build a FileIndex manually from a single directory (no recursion)."""
    checker = RequirementsChecker()
    patterns = set(checker.patterns)
    import fnmatch
    files = set()
    for fname in os.listdir(dirpath):
        for pat in patterns:
            if fnmatch.fnmatch(fname, pat):
                files.add(fname)
                break
    if files:
        return {dirpath: files}
    return {}


def _run(dirpath):
    # type: (str) -> list
    checker = RequirementsChecker()
    index = _index_from_dir(dirpath)
    return checker.check(index, dirpath)


def _run_single(fixture_name):
    # type: (str) -> list
    return _run(FIXTURES)


# ---------------------------------------------------------------------------
# Helpers to run checker on a single named fixture
# ---------------------------------------------------------------------------

def _check_fixture(fname):
    # type: (str) -> list
    """Run RequirementsChecker against a specific fixture file only."""
    checker = RequirementsChecker()
    index = {FIXTURES: {fname}}
    return checker.check(index, FIXTURES)


class TestRequirementsCheckerGood:
    def test_good_file_no_findings(self):
        findings = _check_fixture("requirements-good.txt")
        assert findings == [], "Expected 0 findings for all-==pinned-with-hash file"

    def test_extras_syntax_no_findings(self):
        findings = _check_fixture("requirements-extras.txt")
        assert findings == [], "package[extra]==1.0.0 --hash=... should produce 0 findings"

    def test_comments_only_no_findings(self):
        findings = _check_fixture("requirements-comments_only.txt")
        assert findings == [], "Comments and options only should produce 0 findings"

    def test_includes_only_no_findings(self):
        findings = _check_fixture("requirements-includes.txt")
        assert findings == [], "-r and -e lines should be skipped"

    def test_requirements_dev_filename_pattern(self):
        """requirements-dev.txt should be matched by the requirements*.txt pattern."""
        findings = _check_fixture("requirements-dev.txt")
        assert findings == [], "requirements-dev.txt with valid pins+hashes should have 0 findings"


class TestRequirementsCheckerBad:
    def test_bad_file_has_findings(self):
        findings = _check_fixture("requirements-bad.txt")
        assert len(findings) > 0

    def test_bad_file_unpinned_is_error(self):
        findings = _check_fixture("requirements-bad.txt")
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert len(errors) >= 2, "flask>=2.0.0 and bare 'requests' and django~=4.2 should all be ERRORs"

    def test_bad_file_ge_operator_is_error(self):
        findings = _check_fixture("requirements-bad.txt")
        errors = [f for f in findings if f.severity == Severity.ERROR]
        paths_msgs = [(f.path, f.message) for f in errors]
        has_ge = any("flask" in msg or ">=" in msg for _, msg in paths_msgs)
        assert has_ge, "flask>=2.0.0 should produce an ERROR finding"

    def test_bad_file_bare_name_is_error(self):
        findings = _check_fixture("requirements-bad.txt")
        errors = [f for f in findings if f.severity == Severity.ERROR]
        msgs = [f.message for f in errors]
        has_bare = any("requests" in m for m in msgs)
        assert has_bare, "bare 'requests' (no version) should produce an ERROR finding"

    def test_bad_file_tilde_operator_is_error(self):
        findings = _check_fixture("requirements-bad.txt")
        errors = [f for f in findings if f.severity == Severity.ERROR]
        msgs = [f.message for f in errors]
        has_tilde = any("django" in m or "~=" in m for m in msgs)
        assert has_tilde, "django~=4.2 should produce an ERROR finding"

    def test_findings_have_line_numbers(self):
        findings = _check_fixture("requirements-bad.txt")
        for f in findings:
            assert f.line > 0, "All findings should have line numbers >= 1"

    def test_findings_reference_correct_checker(self):
        findings = _check_fixture("requirements-bad.txt")
        for f in findings:
            assert f.checker == "requirements"


class TestRequirementsCheckerNoHash:
    def test_no_hash_produces_warnings(self):
        findings = _check_fixture("requirements-bad_no_hash.txt")
        assert len(findings) == 3, "Three ==pins without --hash should each get a WARNING"

    def test_no_hash_severity_is_warning(self):
        findings = _check_fixture("requirements-bad_no_hash.txt")
        for f in findings:
            assert f.severity == Severity.WARNING, "Missing hash should be WARNING not ERROR"

    def test_no_hash_not_an_error(self):
        findings = _check_fixture("requirements-bad_no_hash.txt")
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert errors == []


class TestRequirementsCheckerMixed:
    def test_mixed_file_has_both_errors_and_warnings(self):
        findings = _check_fixture("requirements-mixed.txt")
        severities = {f.severity for f in findings}
        # requests>=2.0.0 -> ERROR, django (bare) -> ERROR; the two ==pinned entries with
        # hash are fine; no hash warnings are NOT expected here because the ==pins DO have hashes
        assert Severity.ERROR in severities

    def test_mixed_file_pinned_with_hash_not_flagged(self):
        """flask==2.3.2 --hash=... and certifi==... --hash=... should be clean."""
        findings = _check_fixture("requirements-mixed.txt")
        msgs = [f.message for f in findings]
        assert not any("flask" in m for m in msgs), "flask pinned+hash line should not produce findings"
        assert not any("certifi" in m for m in msgs), "certifi pinned+hash line should not produce findings"


class TestRequirementsCheckerEmptyDir:
    def test_empty_dir_no_findings(self):
        tmpdir = tempfile.mkdtemp()
        try:
            checker = RequirementsChecker()
            findings = checker.check({}, tmpdir)
            assert findings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_no_matching_files_no_findings(self):
        tmpdir = tempfile.mkdtemp()
        try:
            checker = RequirementsChecker()
            # Index with a non-matching file
            index = {tmpdir: {"setup.py"}}
            findings = checker.check(index, tmpdir)
            assert findings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestRequirementsCheckerPatterns:
    def test_patterns_include_glob(self):
        checker = RequirementsChecker()
        import fnmatch
        patterns = checker.patterns
        # requirements.txt, requirements-dev.txt, requirements-prod.txt should all match
        for fname in ["requirements.txt", "requirements-dev.txt", "requirements-prod.txt",
                      "requirements-test.txt"]:
            assert any(fnmatch.fnmatch(fname, p) for p in patterns), \
                "{} should match a requirements checker pattern".format(fname)

    def test_patterns_exclude_non_requirements(self):
        checker = RequirementsChecker()
        import fnmatch
        patterns = checker.patterns
        for fname in ["setup.py", "pyproject.toml", "Makefile"]:
            assert not any(fnmatch.fnmatch(fname, p) for p in patterns), \
                "{} should NOT match requirements checker patterns".format(fname)


class TestRequirementsCheckerPaths:
    def test_finding_path_is_relative(self):
        findings = _check_fixture("requirements-bad.txt")
        for f in findings:
            assert not os.path.isabs(f.path), "Finding paths should be relative, not absolute"

    def test_finding_path_contains_filename(self):
        findings = _check_fixture("requirements-bad.txt")
        for f in findings:
            assert "requirements-bad.txt" in f.path


class TestRequirementsCheckerURLs:
    def test_url_lines_skipped(self):
        """Lines starting with http:// should be skipped."""
        tmpdir = tempfile.mkdtemp()
        try:
            req_path = os.path.join(tmpdir, "requirements.txt")
            with open(req_path, "w") as fh:
                fh.write("http://example.com/some-package.tar.gz\n")
                fh.write("https://example.com/other.whl\n")
            checker = RequirementsChecker()
            index = {tmpdir: {"requirements.txt"}}
            findings = checker.check(index, tmpdir)
            assert findings == [], "URL lines should be skipped"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
