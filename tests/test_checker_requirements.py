"""Tests for the requirements checker."""

import os
import tempfile
import shutil


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

    def test_includes_r_skipped_e_flagged(self):
        findings = _check_fixture("requirements-includes.txt")
        # -r lines are skipped, but -e (editable git install) is flagged
        assert len(findings) == 1
        assert "git+https" in findings[0].message

    def test_requirements_dev_filename_pattern(self):
        """requirements-dev.txt should be matched by the requirements*.txt pattern."""
        findings = _check_fixture("requirements-dev.txt")
        assert findings == [], "requirements-dev.txt with valid pins+hashes should have 0 findings"


class TestRequirementsCheckerBad:
    def test_bad_file_has_findings(self):
        findings = _check_fixture("requirements-bad.txt")
        assert len(findings) > 0

    def test_bad_file_unpinned_has_findings(self):
        findings = _check_fixture("requirements-bad.txt")
        assert len(findings) >= 2, "flask>=2.0.0 and bare 'requests' and django~=4.2 should all produce findings"

    def test_bad_file_ge_operator_is_error(self):
        findings = _check_fixture("requirements-bad.txt")
        msgs = [f.message for f in findings]
        has_ge = any("flask" in msg or ">=" in msg for msg in msgs)
        assert has_ge, "flask>=2.0.0 should produce a finding"

    def test_bad_file_bare_name_is_error(self):
        findings = _check_fixture("requirements-bad.txt")
        msgs = [f.message for f in findings]
        has_bare = any("requests" in m for m in msgs)
        assert has_bare, "bare 'requests' (no version) should produce a finding"

    def test_bad_file_tilde_operator_is_error(self):
        findings = _check_fixture("requirements-bad.txt")
        msgs = [f.message for f in findings]
        has_tilde = any("django" in m or "~=" in m for m in msgs)
        assert has_tilde, "django~=4.2 should produce a finding"

    def test_findings_have_line_numbers(self):
        findings = _check_fixture("requirements-bad.txt")
        for f in findings:
            assert f.line > 0, "All findings should have line numbers >= 1"

    def test_findings_reference_correct_checker(self):
        findings = _check_fixture("requirements-bad.txt")
        for f in findings:
            assert f.checker == "requirements"


class TestRequirementsCheckerNoHash:
    def test_no_hash_produces_findings(self):
        findings = _check_fixture("requirements-bad_no_hash.txt")
        assert len(findings) == 3, "Three ==pins without --hash should each get a finding"


class TestRequirementsCheckerMixed:
    def test_mixed_file_has_findings(self):
        findings = _check_fixture("requirements-mixed.txt")
        # requests>=2.0.0 -> finding, django (bare) -> finding; the two ==pinned entries with
        # hash are fine; no hash findings are NOT expected here because the ==pins DO have hashes
        assert len(findings) > 0

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
    def test_url_lines_flagged(self):
        """URL deps are not content-addressed and should be flagged."""
        tmpdir = tempfile.mkdtemp()
        try:
            req_path = os.path.join(tmpdir, "requirements.txt")
            with open(req_path, "w") as fh:
                fh.write("http://example.com/some-package.tar.gz\n")
                fh.write("https://example.com/other.whl\n")
            checker = RequirementsChecker()
            index = {tmpdir: {"requirements.txt"}}
            findings = checker.check(index, tmpdir)
            assert len(findings) == 2, "URL lines should be flagged"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestRequirementsCheckerPEP508:
    """PEP 508 direct references in requirements.txt."""

    def _check(self, *lines):
        tmpdir = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmpdir, "requirements.txt"), "w") as fh:
                fh.write("\n".join(lines) + "\n")
            checker = RequirementsChecker()
            index = {tmpdir: {"requirements.txt"}}
            return checker.check(index, tmpdir)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_mutable_git_tag_flagged(self):
        """git+https://...@v0.3.0 is a movable tag — exactly 1 pin error."""
        findings = self._check("lib @ git+https://github.com/example/lib.git@v0.3.0")
        assert len(findings) == 1
        assert "lib" in findings[0].message
        assert "not pinned" in findings[0].message or "mutable" in findings[0].message.lower()

    def test_mutable_git_branch_flagged(self):
        """git+https://...@main is a branch — exactly 1 pin error."""
        findings = self._check("lib @ git+https://github.com/example/lib.git@main")
        assert len(findings) == 1
        assert "lib" in findings[0].message

    def test_immutable_git_sha_clean(self):
        """git+https://...@<sha> is immutable — VCS deps can't be hashed, so 0 findings."""
        findings = self._check(
            "lib @ git+https://github.com/example/lib.git@a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4"
        )
        assert len(findings) == 0

    def test_immutable_git_sha_with_hash_also_clean(self):
        """Commit SHA + --hash (if someone manages it) — still 0 findings."""
        findings = self._check(
            "lib @ git+https://github.com/example/lib.git@a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4"
            " --hash=sha256:abcdef1234567890"
        )
        assert len(findings) == 0

    def test_archive_url_without_hash_flagged(self):
        """https:// URL without --hash or #sha256= — exactly 1 finding."""
        findings = self._check("lib @ https://example.com/lib-1.0.tar.gz")
        assert len(findings) == 1
        assert "lib" in findings[0].message

    def test_archive_url_with_hash_clean(self):
        """https:// URL with --hash — 0 findings."""
        findings = self._check(
            "lib @ https://example.com/lib-1.0.tar.gz --hash=sha256:abcdef1234567890"
        )
        assert len(findings) == 0

    def test_archive_url_with_fragment_hash_clean(self):
        """https:// URL with #sha256= fragment — 0 findings."""
        findings = self._check(
            "lib @ https://example.com/lib-1.0.tar.gz#sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
        assert len(findings) == 0

    def test_archive_url_with_sha1_fragment_clean(self):
        """https:// URL with #sha1= fragment — 0 findings."""
        findings = self._check(
            "lib @ https://example.com/lib-1.0.tar.gz#sha1=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        )
        assert len(findings) == 0

    def test_archive_url_with_sha224_fragment_clean(self):
        """https:// URL with #sha224= fragment — 0 findings."""
        findings = self._check(
            "lib @ https://example.com/lib-1.0.tar.gz#sha224=cccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
        )
        assert len(findings) == 0

    def test_archive_url_with_hash_after_subdirectory(self):
        """#subdirectory=src&sha256=abc — hash not first in fragment."""
        findings = self._check(
            "lib @ https://example.com/lib-1.0.tar.gz#subdirectory=src&sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
        assert len(findings) == 0
