"""Tests for the four JS ecosystem checkers: package_json, package_lock, yarn_lock, pnpm_lock."""

import json
import os
import shutil
import tempfile

from pinstack.checkers.package_json import PackageJsonChecker
from pinstack.checkers.package_lock import PackageLockChecker
from pinstack.checkers.yarn_lock import YarnLockChecker
from pinstack.checkers.pnpm_lock import PnpmLockChecker

JS_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "js")


def _check_fixture_dir(checker, subpath, extra_files=None):
    # type: (object, str, set) -> list
    """Run a checker against a single fixture directory, rooted at that directory."""
    dirpath = os.path.join(JS_FIXTURES, subpath)
    fname = checker.patterns[0]
    files = {fname}
    if extra_files:
        files = files | extra_files
    index = {dirpath: files}
    return checker.check(index, dirpath)


def _check_empty(checker):
    # type: (object, ) -> list
    return checker.check({}, "/tmp")


# ---------------------------------------------------------------------------
# package.json
# ---------------------------------------------------------------------------


class TestPackageJsonGood:
    def test_exact_versions_no_findings(self):
        findings = _check_fixture_dir(
            PackageJsonChecker(), "package_json/good", extra_files={"package-lock.json"}
        )
        assert findings == [], "Exact versions should produce 0 findings"

    def test_no_dependencies_no_findings(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/no_deps")
        assert findings == [], (
            "package.json with no dep sections should produce 0 findings"
        )

    def test_workspace_protocol_not_flagged(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/workspace")
        msgs = [f.message for f in findings]
        assert not any("workspace" in m for m in msgs), (
            "workspace:* should not be flagged"
        )
        assert not any("shared-lib" in m for m in msgs)

    def test_file_protocol_not_flagged(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/workspace")
        msgs = [f.message for f in findings]
        assert not any("local-utils" in m for m in msgs), (
            "file:../utils should not be flagged"
        )

    def test_url_version_flagged_as_not_content_addressed(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/workspace")
        url_findings = [f for f in findings if "remote-pkg" in f.message]
        assert len(url_findings) == 1, (
            "https:// URL should be flagged as not content-addressed"
        )
        assert "not content-addressed" in url_findings[0].message

    def test_empty_index_no_findings(self):
        assert _check_empty(PackageJsonChecker()) == []


class TestPackageJsonNonExactForms:
    """npm range forms beyond ^ ~ > < must not pass as pinned."""

    def setup_method(self):
        self.findings = _check_fixture_dir(
            PackageJsonChecker(),
            "package_json/ranges",
            extra_files={"package-lock.json"},
        )
        self.by_pkg = {f.message.split("'")[1]: f for f in self.findings}

    def test_eleven_findings(self):
        assert sorted(self.by_pkg) == list("abcdefghijk"), [
            f.message for f in self.findings
        ]

    def test_wildcards_tags_and_ranges_not_exact_semver(self):
        for pkg in "abcdefk":
            assert "not exact semver" in self.by_pkg[pkg].message, pkg
            assert not self.by_pkg[pkg].integrity

    def test_git_sources_not_content_addressed(self):
        """user/repo, scp-style git@ and ssh:// all fetch a moving ref."""
        for pkg in "ghi":
            assert "not content-addressed" in self.by_pkg[pkg].message, pkg
            assert self.by_pkg[pkg].integrity

    def test_alias_range_unpinned(self):
        assert "unpinned version 'npm:other@^1.0.0'" in self.by_pkg["j"].message


def _classify(version):
    # type: (object) -> tuple
    from pinstack.checkers.package_json import _is_unpinned

    return _is_unpinned(version)


class TestPackageJsonVersionAllowlist:
    """Unit tests for the version-spec allowlist."""

    _SHA = "ab12" * 10

    def test_exact_semver_accepted(self):
        for v in ("1.2.3", "0.0.0", "1.2.3-rc.1", "1.2.3+build.5", "v1.2.3", "=1.2.3"):
            assert _classify(v) == (False, ""), v

    def test_local_references_accepted(self):
        for v in (
            "workspace:*",
            "file:../x",
            "link:../x",
            "portal:../x",
            "./x",
            "../x",
            "/abs/x",
            "~/x",
        ):
            assert _classify(v) == (False, ""), v

    def test_alias_exact_accepted(self):
        assert _classify("npm:other@1.0.0") == (False, "")
        assert _classify("npm:@scope/other@1.0.0") == (False, "")

    def test_alias_without_version_flagged(self):
        assert _classify("npm:other") == (True, "not exact semver")
        assert _classify("npm:@scope/other") == (True, "not exact semver")
        assert _classify("npm:other@latest") == (True, "not exact semver")

    def test_git_with_commit_sha_accepted(self):
        for v in (
            "git+https://github.com/u/r.git#" + self._SHA,
            "git+ssh://git@github.com/u/r.git#" + self._SHA,
            "github:u/r#" + self._SHA,
            "u/r#" + self._SHA,
        ):
            assert _classify(v) == (False, ""), v

    def test_git_with_moving_ref_flagged(self):
        for v in (
            "git+https://github.com/u/r.git",
            "git+https://github.com/u/r.git#main",
            "github:u/r#v1.0.0",
            "u/r#semver:^1.0.0",
            "u/r#" + self._SHA[:7],
            "gitlab:u/r",
            "git://github.com/u/r.git",
        ):
            assert _classify(v) == (True, "not content-addressed"), v

    def test_tarball_url_flagged(self):
        assert _classify("https://x.test/p.tgz") == (True, "not content-addressed")
        assert _classify("https://x.test/p.tgz#" + self._SHA) == (
            True,
            "not content-addressed",
        )

    def test_comparator_ranges_unpinned(self):
        for v in ("^1.0.0", "~1.0.0", ">=1.0.0", "<2", ">=1.0.0 <2.0.0"):
            assert _classify(v) == (True, "unpinned version"), v

    def test_other_forms_not_exact(self):
        for v in ("*", "x", "", "  ", "latest", "next", "1", "1.2", "1.2.x", "1.x.x"):
            assert _classify(v) == (True, "not exact semver"), repr(v)

    def test_non_string_flagged(self):
        """npm rejects non-string specs; the gate must not treat them as pinned."""
        for v in (None, 1, 1.5, {"version": "1.0.0"}):
            assert _classify(v) == (True, "not exact semver"), repr(v)


class TestPackageJsonBad:
    def setup_method(self):
        self.findings = _check_fixture_dir(PackageJsonChecker(), "package_json/bad")

    def test_has_four_findings(self):
        # 4 pinning errors + 1 lock-file companion warning = 5 total
        assert len(self.findings) == 5, "Expected 5 findings, got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_caret_express_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("express" in m for m in msgs), "express ^4.18.2 should be flagged"

    def test_tilde_lodash_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("lodash" in m for m in msgs), "lodash ~4.17.21 should be flagged"

    def test_gte_jest_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("jest" in m for m in msgs), "jest >=29.0.0 should be flagged"

    def test_caret_react_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("react" in m for m in msgs), "react ^18.0.0 should be flagged"

    def test_all_findings_count(self):
        # 4 pinning errors + 1 missing lock file finding = 5
        assert len(self.findings) == 5

    def test_findings_line_zero(self):
        for f in self.findings:
            assert f.line == 0, "JSON findings should have line=0"

    def test_checker_name(self):
        for f in self.findings:
            assert f.checker == "package_json"

    def test_paths_are_relative(self):
        for f in self.findings:
            assert not os.path.isabs(f.path)


# ---------------------------------------------------------------------------
# package-lock.json
# ---------------------------------------------------------------------------


class TestPackageLockGood:
    def test_all_integrity_no_findings(self):
        findings = _check_fixture_dir(PackageLockChecker(), "package_lock/good")
        assert findings == [], "All packages with integrity should produce 0 findings"

    def test_empty_index_no_findings(self):
        assert _check_empty(PackageLockChecker()) == []

    def test_root_entry_skipped(self):
        # good fixture has root "" entry with no integrity — should not produce a finding
        findings = _check_fixture_dir(PackageLockChecker(), "package_lock/good")
        assert findings == []

    def test_link_packages_skipped(self):
        findings = _check_fixture_dir(PackageLockChecker(), "package_lock/link")
        assert findings == [], (
            "link:true packages without integrity should not be flagged"
        )


class TestPackageLockBad:
    def setup_method(self):
        self.findings = _check_fixture_dir(PackageLockChecker(), "package_lock/bad")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Expected 1 finding for lodash missing integrity, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_finding_message_contains_package(self):
        assert "lodash" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "package_lock"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)


class TestPackageLockV1Good:
    def test_lockfile_v1_good(self):
        """v1 lockfile with integrity on all deps should produce 0 findings."""
        findings = _check_fixture_dir(PackageLockChecker(), "package_lock/v1_good")
        assert findings == [], (
            "v1 lockfile with all integrity hashes should produce 0 findings, got: {}".format(
                [f.message for f in findings]
            )
        )


class TestPackageLockV1Bad:
    def setup_method(self):
        self.findings = _check_fixture_dir(PackageLockChecker(), "package_lock/v1_bad")

    def test_lockfile_v1_bad(self):
        """v1 lockfile with dep missing integrity should produce 1 finding."""
        assert len(self.findings) == 1, (
            "Expected 1 finding for lodash missing integrity, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_finding_message_contains_package(self):
        assert "lodash" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "package_lock"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)


# ---------------------------------------------------------------------------
# yarn.lock
# ---------------------------------------------------------------------------


class TestYarnLockGood:
    def test_all_integrity_no_findings(self):
        findings = _check_fixture_dir(YarnLockChecker(), "yarn_lock/good")
        assert findings == [], "All entries with integrity should produce 0 findings"

    def test_empty_index_no_findings(self):
        assert _check_empty(YarnLockChecker()) == []


class TestYarnBerryChecksum:
    def test_yarn_berry_checksum(self):
        """Yarn Berry lockfiles using checksum: instead of integrity should produce 0 findings."""
        import tempfile

        content = (
            "__metadata:\n"
            "  version: 6\n"
            "  cacheKey: 8\n"
            "\n"
            '"express@npm:4.18.2":\n'
            "  version: 4.18.2\n"
            '  resolution: "express@npm:4.18.2"\n'
            "  checksum: 10/abc123def456\n"
            "\n"
            '"lodash@npm:4.17.21":\n'
            "  version: 4.17.21\n"
            '  resolution: "lodash@npm:4.17.21"\n'
            "  checksum: 10/789xyz\n"
        )
        tmpdir = tempfile.mkdtemp()
        try:
            lock_path = os.path.join(tmpdir, "yarn.lock")
            with open(lock_path, "w") as fh:
                fh.write(content)
            index = {tmpdir: {"yarn.lock"}}
            findings = YarnLockChecker().check(index, tmpdir)
            assert findings == [], (
                "Yarn Berry checksum: should be accepted, got: {}".format(
                    [f.message for f in findings]
                )
            )
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)


class TestYarnLockBad:
    def setup_method(self):
        self.findings = _check_fixture_dir(YarnLockChecker(), "yarn_lock/bad")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Expected 1 finding for lodash missing integrity, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_finding_message_contains_package(self):
        assert "lodash" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "yarn_lock"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)

    def test_finding_has_line_number(self):
        assert self.findings[0].line > 0


# ---------------------------------------------------------------------------
# pnpm-lock.yaml
# ---------------------------------------------------------------------------


class TestPnpmLockGood:
    def test_all_integrity_no_findings(self):
        findings = _check_fixture_dir(PnpmLockChecker(), "pnpm_lock/good")
        assert findings == [], "All packages with integrity should produce 0 findings"

    def test_empty_index_no_findings(self):
        assert _check_empty(PnpmLockChecker()) == []


class TestPnpmLockBad:
    def setup_method(self):
        self.findings = _check_fixture_dir(PnpmLockChecker(), "pnpm_lock/bad")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Expected 1 finding for lodash missing integrity, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_finding_message_contains_package(self):
        assert "lodash" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "pnpm_lock"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)

    def test_finding_has_line_number(self):
        assert self.findings[0].line > 0


# ---------------------------------------------------------------------------
# package.json lock file companion check tests
# ---------------------------------------------------------------------------

_PACKAGE_JSON_WITH_DEPS = json.dumps(
    {
        "name": "my-app",
        "version": "1.0.0",
        "dependencies": {"express": "4.18.2"},
    }
)

_PACKAGE_JSON_NO_DEPS = json.dumps(
    {
        "name": "my-app",
        "version": "1.0.0",
    }
)


def _make_package_json(tmpdir, content):
    # type: (str, str) -> None
    path = os.path.join(tmpdir, "package.json")
    with open(path, "w") as fh:
        fh.write(content)


class TestPackageJsonLockFileCheck:
    def test_no_lock_file_warns(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_WITH_DEPS)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert len(lock_warnings) == 1
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_package_lock_satisfies(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_WITH_DEPS)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "package-lock.json"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert lock_warnings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_yarn_lock_satisfies(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_WITH_DEPS)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "yarn.lock"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert lock_warnings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_pnpm_lock_satisfies(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_WITH_DEPS)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "pnpm-lock.yaml"}}
            findings = checker.check(index, tmpdir)
            lock_warnings = [f for f in findings if "lock file" in f.message]
            assert lock_warnings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_no_deps_no_warning(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_NO_DEPS)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json"}}
            findings = checker.check(index, tmpdir)
            assert findings == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_lock_file_finding_present(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_WITH_DEPS)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json"}}
            findings = checker.check(index, tmpdir)
            lock_findings = [f for f in findings if "lock file" in f.message]
            assert len(lock_findings) == 1
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# package.json lock file cross-reference tests
# ---------------------------------------------------------------------------

_PACKAGE_JSON_TWO_DEPS = json.dumps(
    {
        "name": "my-app",
        "version": "1.0.0",
        "dependencies": {"express": "4.18.2", "lodash": "4.17.21"},
    }
)


class TestPackageJsonLockFileCrossRef:
    def test_dep_missing_from_package_lock(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_TWO_DEPS)
            lock_path = os.path.join(tmpdir, "package-lock.json")
            lock_data = {
                "name": "my-app",
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "my-app", "version": "1.0.0"},
                    "node_modules/express": {
                        "version": "4.18.2",
                        "integrity": "sha512-abc",
                    },
                },
            }
            with open(lock_path, "w") as fh:
                json.dump(lock_data, fh)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "package-lock.json"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert len(cross_ref) == 1
            assert "lodash" in cross_ref[0].message
            assert "package-lock.json" in cross_ref[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dep_present_in_package_lock(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_TWO_DEPS)
            lock_path = os.path.join(tmpdir, "package-lock.json")
            lock_data = {
                "name": "my-app",
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "my-app", "version": "1.0.0"},
                    "node_modules/express": {
                        "version": "4.18.2",
                        "integrity": "sha512-abc",
                    },
                    "node_modules/lodash": {
                        "version": "4.17.21",
                        "integrity": "sha512-def",
                    },
                },
            }
            with open(lock_path, "w") as fh:
                json.dump(lock_data, fh)
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "package-lock.json"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert cross_ref == []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dep_missing_from_yarn_lock(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_TWO_DEPS)
            lock_path = os.path.join(tmpdir, "yarn.lock")
            with open(lock_path, "w") as fh:
                fh.write(
                    "# yarn lockfile v1\n"
                    "\n"
                    "express@4.18.2:\n"
                    '  version "4.18.2"\n'
                    "  integrity sha512-abc\n"
                )
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "yarn.lock"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert len(cross_ref) == 1
            assert "lodash" in cross_ref[0].message
            assert "yarn.lock" in cross_ref[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dep_missing_from_pnpm_lock(self):
        tmpdir = tempfile.mkdtemp()
        try:
            _make_package_json(tmpdir, _PACKAGE_JSON_TWO_DEPS)
            lock_path = os.path.join(tmpdir, "pnpm-lock.yaml")
            with open(lock_path, "w") as fh:
                fh.write(
                    "lockfileVersion: 5\n"
                    "\n"
                    "packages:\n"
                    "  /express@4.18.2:\n"
                    "    resolution: {integrity: sha512-abc}\n"
                )
            checker = PackageJsonChecker()
            index = {tmpdir: {"package.json", "pnpm-lock.yaml"}}
            findings = checker.check(index, tmpdir)
            cross_ref = [f for f in findings if "stale" in f.message]
            assert len(cross_ref) == 1
            assert "lodash" in cross_ref[0].message
            assert "pnpm-lock.yaml" in cross_ref[0].message
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
