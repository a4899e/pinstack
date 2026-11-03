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
        assert findings == [], (
            "package[extra]==1.0.0 --hash=... should produce 0 findings"
        )

    def test_comments_only_no_findings(self):
        findings = _check_fixture("requirements-comments_only.txt")
        assert findings == [], "Comments and options only should produce 0 findings"

    def test_includes_followed_e_flagged(self):
        """Review L2: -r targets are checked; these two do not exist, so they are reported."""
        findings = _check_fixture("requirements-includes.txt")
        assert [f.message for f in findings] == [
            "-r 'base.txt' does not exist, so its requirements cannot be checked",
            "-r 'other-requirements.txt' does not exist, so its requirements cannot be checked",
            "'git+https://github.com/example/pkg.git#egg=pkg' is not pinned with ==;"
            " use package==version",
        ]

    def test_requirements_dev_filename_pattern(self):
        """requirements-dev.txt should be matched by the requirements*.txt pattern."""
        findings = _check_fixture("requirements-dev.txt")
        assert findings == [], (
            "requirements-dev.txt with valid pins+hashes should have 0 findings"
        )


class TestRequirementsCheckerBad:
    def test_bad_file_has_findings(self):
        findings = _check_fixture("requirements-bad.txt")
        assert len(findings) > 0

    def test_bad_file_unpinned_has_findings(self):
        findings = _check_fixture("requirements-bad.txt")
        assert len(findings) >= 2, (
            "flask>=2.0.0 and bare 'requests' and django~=4.2 should all produce findings"
        )

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
        assert len(findings) == 3, (
            "Three ==pins without --hash should each get a finding"
        )


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
        assert not any("flask" in m for m in msgs), (
            "flask pinned+hash line should not produce findings"
        )
        assert not any("certifi" in m for m in msgs), (
            "certifi pinned+hash line should not produce findings"
        )


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
        for fname in [
            "requirements.txt",
            "requirements-dev.txt",
            "requirements-prod.txt",
            "requirements-test.txt",
        ]:
            assert any(fnmatch.fnmatch(fname, p) for p in patterns), (
                "{} should match a requirements checker pattern".format(fname)
            )

    def test_patterns_exclude_non_requirements(self):
        checker = RequirementsChecker()
        import fnmatch

        patterns = checker.patterns
        for fname in ["setup.py", "pyproject.toml", "Makefile"]:
            assert not any(fnmatch.fnmatch(fname, p) for p in patterns), (
                "{} should NOT match requirements checker patterns".format(fname)
            )


class TestRequirementsCheckerPaths:
    def test_finding_path_is_relative(self):
        findings = _check_fixture("requirements-bad.txt")
        for f in findings:
            assert not os.path.isabs(f.path), (
                "Finding paths should be relative, not absolute"
            )

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
        assert (
            "not pinned" in findings[0].message
            or "mutable" in findings[0].message.lower()
        )

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
            "lib @ https://example.com/lib-1.0.tar.gz --hash=sha256:" + "ab" * 32
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


def _scan(subdir):
    # type: (str) -> list
    """Index a fixture tree the way the CLI does and run the checker over it."""
    from pinstack.core import build_index

    root = os.path.join(FIXTURES, subdir)
    index = build_index(root, set(RequirementsChecker.patterns))
    return [
        (f.path.replace(os.sep, "/"), f.line, f.message)
        for f in RequirementsChecker().check(index, root)
    ]


class TestRequirementsIncludes:
    """Review repro L2: -r includes outside the requirements*.txt glob."""

    def test_repro_include_outside_glob(self):
        assert _scan("include_outside_glob") == [
            (
                "reqs/base.txt",
                1,
                "'requests' is not pinned with ==; use package==version",
            )
        ]

    def test_django_layout_and_cycle(self):
        """requirements/base.txt and production.txt include each other; each is checked once."""
        assert _scan("django_layout") == [
            (
                "requirements/base.txt",
                2,
                "'django' is not pinned with ==; use package==version",
            )
        ]

    def test_includes_that_cannot_be_checked(self):
        assert _scan("include_edges") == [
            (
                "requirements.txt",
                2,
                "-r 'missing.txt' does not exist, so its requirements cannot be checked",
            ),
            (
                "requirements.txt",
                3,
                "-r 'https://example.invalid/reqs.txt' is fetched from a URL, so its"
                " contents cannot be checked",
            ),
            (
                "requirements.txt",
                4,
                "-r '${REQS_DIR}/extra.txt' uses an environment variable, so the file"
                " cannot be checked",
            ),
            (
                "requirements.txt",
                5,
                "-r '../../outside.txt' is outside the scanned directory, so it cannot"
                " be checked",
            ),
        ]

    def test_symlink_out_of_root_is_reported(self):
        outside = tempfile.mkdtemp()
        root = tempfile.mkdtemp()
        try:
            with open(os.path.join(outside, "secret.txt"), "w") as fh:
                fh.write("anything>=1\n")
            os.symlink(outside, os.path.join(root, "linked"))
            with open(os.path.join(root, "requirements.txt"), "w") as fh:
                fh.write("-r linked/secret.txt\n")
            findings = RequirementsChecker().check({root: {"requirements.txt"}}, root)
        finally:
            shutil.rmtree(outside, ignore_errors=True)
            shutil.rmtree(root, ignore_errors=True)
        assert [f.message for f in findings] == [
            "-r 'linked/secret.txt' is outside the scanned directory, so it cannot be checked"
        ]


class TestRequirementsHashValidation:
    """Review L2: --hash values were never validated."""

    def setup_method(self):
        self.by_line = {line: m for _, line, m in _scan("hashes")}

    def test_valid_hashes_pass(self):
        """sha256 and sha512 with full digests, in --hash=v and --hash v form."""
        assert 1 not in self.by_line and 2 not in self.by_line and 7 not in self.by_line

    def test_weak_algorithm(self):
        assert "uses md5, which pip does not accept for --hash" in self.by_line[3]

    def test_short_digest(self):
        assert (
            "'sha256:abc': sha256 hash has wrong length: expected 64 hex chars, got 3"
            in self.by_line[4]
        )

    def test_non_hex_digest(self):
        assert "sha256 hash contains non-hex characters" in self.by_line[5]

    def test_missing_algorithm(self):
        assert "is not <algorithm>:<hex digest>" in self.by_line[6]

    def test_one_bad_hash_among_good_is_reported(self):
        assert "uses sha1, which pip does not accept" in self.by_line[8]

    def test_url_reference_hash_validated(self):
        from pinstack.checkers.requirements import _analyze_line

        results = _analyze_line(
            "pkg @ https://example.invalid/p.tar.gz --hash=sha256:abc"
        )
        assert [r.kind for r in results] == ["hash_invalid"]


def _req(text, extra=None):
    # type: (str, dict) -> list
    """Check one requirements.txt (plus any extra files) in a temporary root."""
    d = tempfile.mkdtemp()
    try:
        for name, content in dict({"requirements.txt": text}, **(extra or {})).items():
            with open(os.path.join(d, name), "w") as fh:
                fh.write(content)
        findings = RequirementsChecker().check({d: {"requirements.txt"}}, d)
        return [(f.path, f.line, f.message) for f in findings]
    finally:
        shutil.rmtree(d, ignore_errors=True)


_H = "--hash=sha256:" + "ab" * 32


class TestRequirementsPipGrammar:
    """Local review of L2: read lines the way pip's req_file does."""

    def test_url_fragment_does_not_hide_hash(self):
        assert _req("pkg @ https://x.invalid/p.tar.gz#egg=pkg " + _H + "\n") == []

    def test_comment_line_never_continues(self):
        """pip does not join a comment ending in a backslash with the next line."""
        assert _req("# pinned deps \\\nrequests>=2\n") == [
            (
                "requirements.txt",
                2,
                "'requests' is not pinned with ==; use package==version",
            )
        ]

    def test_long_editable(self):
        findings = _req("--editable git+https://github.com/x/y.git#egg=y\n")
        assert [m for _, _, m in findings] == [
            "'git+https://github.com/x/y.git#egg=y' is not pinned with ==;"
            " use package==version"
        ]

    def test_include_after_other_options(self):
        findings = _req("--pre -r extra.txt\n", {"extra.txt": "flask\n"})
        assert findings == [
            ("extra.txt", 1, "'flask' is not pinned with ==; use package==version")
        ]

    def test_abbreviated_long_option(self):
        findings = _req("--requirem extra.txt\n", {"extra.txt": "flask\n"})
        assert [p for p, _, _ in findings] == ["extra.txt"]

    def test_tab_before_comment_on_include(self):
        findings = _req("-r extra.txt\t# note\n", {"extra.txt": "flask\n"})
        assert [p for p, _, _ in findings] == ["extra.txt"]

    def test_hash_algorithm_is_case_sensitive(self):
        """pip rejects --hash=SHA256:..., so a file using it cannot be installed."""
        findings = _req("foo==1.0 --hash=SHA256:" + "ab" * 32 + "\n")
        assert "uses SHA256, which pip does not accept for --hash" in findings[0][2]

    def test_constraint_files_are_not_requirements(self):
        assert _req("-c constraints.txt\n", {"constraints.txt": "urllib3<3\n"}) == []

    def test_bom_is_ignored(self):
        assert _req("\ufeffrequests>=2\n") == [
            (
                "requirements.txt",
                1,
                "'requests' is not pinned with ==; use package==version",
            )
        ]


class TestRequirementsPipSemantics:
    """Second local review of L2."""

    def test_r_inside_constraints_file_is_followed(self):
        """pip reads -r inside a constraints file as a real requirements file."""
        findings = _req(
            "-c constraints.txt\n",
            {"constraints.txt": "urllib3<3\n-r extra.txt\n", "extra.txt": "evil\n"},
        )
        assert findings == [
            ("extra.txt", 1, "'evil' is not pinned with ==; use package==version")
        ]

    def test_environment_variable_requirement(self):
        findings = _req("${PKG}==1.0 " + _H + "\n")
        assert [m for _, _, m in findings] == [
            "'${PKG}==1.0' uses an environment variable, so the requirement cannot"
            " be verified"
        ]

    def test_local_wheel_needs_a_hash(self):
        assert [m for _, _, m in _req("/tmp/evil.whl\n")] == [
            "'/tmp/evil.whl' is not pinned with ==; use package==version"
        ]
        assert _req("./wheels/pkg-1.0-py3-none-any.whl " + _H + "\n") == []

    def test_hashed_bare_url_is_pinned(self):
        assert _req("https://x.invalid/foo-1.0.tar.gz " + _H + "\n") == []

    def test_spaces_around_exact_operator(self):
        assert _req("foo == 1.0 " + _H + "\n") == []

    def test_only_first_include_per_line(self):
        findings = _req(
            "-r a.txt -r b.txt\n", {"a.txt": "flask\n", "b.txt": "django\n"}
        )
        assert [p for p, _, _ in findings] == ["a.txt"]

    def test_requirement_line_does_not_include(self):
        """pip ignores -r on a line that is itself a requirement."""
        findings = _req("foo==1.0 " + _H + " -r missing.txt\n")
        assert findings == []

    def test_file_scheme_include_is_a_path(self):
        findings = _req("-r file:extra.txt\n", {"extra.txt": "flask\n"})
        assert [p for p, _, _ in findings] == ["extra.txt"]

    def test_unreadable_include_is_reported(self):
        d = tempfile.mkdtemp()
        try:
            os.mkdir(os.path.join(d, "adir"))
            with open(os.path.join(d, "requirements.txt"), "w") as fh:
                fh.write("-r adir\n")
            findings = RequirementsChecker().check({d: {"requirements.txt"}}, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        assert [f.message for f in findings] == ["-r 'adir' could not be read"]


class TestRequirementsOptionAliases:
    """Codex review of L2: an option pinstack misreads must not hide an include."""

    def test_repro_pypi_url_alias(self):
        """--pypi-url is pip's alias for --index-url; the -r after it is followed."""
        assert _scan("pypi_url_alias") == [
            (
                "reqs/base.txt",
                1,
                "'requests' is not pinned with ==; use package==version",
            )
        ]

    def test_short_config_settings(self):
        findings = _req("-C key=value -r extra.txt\n", {"extra.txt": "flask\n"})
        assert [p for p, _, _ in findings] == ["extra.txt"]

    def test_positional_token_does_not_stop_options(self):
        """optparse skips a stray argument, so pip still reads the -r after it."""
        findings = _req("--pre stray -r extra.txt\n", {"extra.txt": "flask\n"})
        assert [p for p, _, _ in findings] == ["extra.txt"]

    def test_double_dash_ends_options(self):
        """After --, pip reads -r as a positional argument and ignores it."""
        assert _req("--pre -- -r extra.txt\n", {"extra.txt": "flask\n"}) == []

    def test_unknown_option_is_reported(self):
        assert _req("--install-option=x -r extra.txt\n", {"extra.txt": "f\n"}) == [
            (
                "requirements.txt",
                1,
                "line has unknown option '--install-option=x', so pinstack cannot"
                " read the rest of it; use only the options pip documents for"
                " requirements files",
            )
        ]

    def test_ambiguous_and_valueless_options_are_reported(self):
        assert "ambiguous option '--p'" in _req("--p -r extra.txt\n")[0][2]
        assert "option '-r' has no value" in _req("--pre -r\n")[0][2]

    def test_unknown_option_after_requirement_is_reported(self):
        findings = _req("foo==1.0 " + _H + " --bogus\n")
        assert [m for _, _, m in findings] == [
            "line has unknown option '--bogus', so pinstack cannot read the rest"
            " of it; use only the options pip documents for requirements files"
        ]


class TestRequirementsOptionReview:
    """Local review of the option-parser fix."""

    def test_environment_variable_in_positional_slot(self):
        """pip expands ${EXTRA} before splitting, so it may hold "-r evil.txt"."""
        assert _req("--pre ${EXTRA}\n") == [
            (
                "requirements.txt",
                1,
                "line has environment variable '${EXTRA}' outside an option value,"
                " so pinstack cannot read the rest of it; use only the options pip"
                " documents for requirements files",
            )
        ]

    def test_environment_variable_in_option_value_is_allowed(self):
        assert _req("--extra-index-url ${PRIVATE_URL}\n") == []

    def test_removed_short_option_is_reported(self):
        assert "unknown option '-Zr'" in _req("-Zr extra.txt\n")[0][2]

    def test_unclosed_quote_is_reported(self):
        assert "options with an unclosed quote" in _req("--pre 'x -r a.txt\n")[0][2]

    def test_pypi_url_equals_and_abbreviation(self):
        for line in (
            "--pypi-url=https://x.invalid -r extra.txt\n",
            "--pypi https://x.invalid -r extra.txt\n",
        ):
            assert [p for p, _, _ in _req(line, {"extra.txt": "flask\n"})] == [
                "extra.txt"
            ]

    def test_lone_dash_is_positional(self):
        findings = _req("--pre - -r extra.txt\n", {"extra.txt": "flask\n"})
        assert [p for p, _, _ in findings] == ["extra.txt"]

    def test_option_problem_in_constraints_file(self):
        """pip reads options in a -c file the same way, so a problem is reported."""
        findings = _req("-c constraints.txt\n", {"constraints.txt": "--bogus\n"})
        assert [(p, m.split(",")[0]) for p, _, m in findings] == [
            ("constraints.txt", "line has unknown option '--bogus'")
        ]
