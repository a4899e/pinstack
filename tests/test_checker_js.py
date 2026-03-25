"""Tests for the four JS ecosystem checkers: package_json, package_lock, yarn_lock, pnpm_lock."""

import os

from pinstack.core import Severity
from pinstack.checkers.package_json import PackageJsonChecker
from pinstack.checkers.package_lock import PackageLockChecker
from pinstack.checkers.yarn_lock import YarnLockChecker
from pinstack.checkers.pnpm_lock import PnpmLockChecker

JS_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "js")


def _check_fixture_dir(checker, subpath):
    # type: (object, str) -> list
    """Run a checker against a single fixture directory, rooted at that directory."""
    dirpath = os.path.join(JS_FIXTURES, subpath)
    fname = checker.patterns[0]
    index = {dirpath: {fname}}
    return checker.check(index, dirpath)


def _check_empty(checker):
    # type: (object, ) -> list
    return checker.check({}, "/tmp")


# ---------------------------------------------------------------------------
# package.json
# ---------------------------------------------------------------------------

class TestPackageJsonGood:
    def test_exact_versions_no_findings(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/good")
        assert findings == [], "Exact versions should produce 0 findings"

    def test_no_dependencies_no_findings(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/no_deps")
        assert findings == [], "package.json with no dep sections should produce 0 findings"

    def test_workspace_protocol_not_flagged(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/workspace")
        msgs = [f.message for f in findings]
        assert not any("workspace" in m for m in msgs), "workspace:* should not be flagged"
        assert not any("shared-lib" in m for m in msgs)

    def test_file_protocol_not_flagged(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/workspace")
        msgs = [f.message for f in findings]
        assert not any("local-utils" in m for m in msgs), "file:../utils should not be flagged"

    def test_url_version_not_flagged(self):
        findings = _check_fixture_dir(PackageJsonChecker(), "package_json/workspace")
        msgs = [f.message for f in findings]
        assert not any("remote-pkg" in m for m in msgs), "https:// URL version should not be flagged"

    def test_empty_index_no_findings(self):
        assert _check_empty(PackageJsonChecker()) == []


class TestPackageJsonBad:
    def setup_method(self):
        self.findings = _check_fixture_dir(PackageJsonChecker(), "package_json/bad")

    def test_has_four_findings(self):
        assert len(self.findings) == 4, "Expected 4 findings, got {}: {}".format(
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

    def test_all_findings_are_errors(self):
        for f in self.findings:
            assert f.severity == Severity.ERROR

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
        assert findings == [], "link:true packages without integrity should not be flagged"


class TestPackageLockBad:
    def setup_method(self):
        self.findings = _check_fixture_dir(PackageLockChecker(), "package_lock/bad")

    def test_one_finding(self):
        assert len(self.findings) == 1, "Expected 1 finding for lodash missing integrity, got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_finding_is_warning(self):
        assert self.findings[0].severity == Severity.WARNING

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


class TestYarnLockBad:
    def setup_method(self):
        self.findings = _check_fixture_dir(YarnLockChecker(), "yarn_lock/bad")

    def test_one_finding(self):
        assert len(self.findings) == 1, "Expected 1 finding for lodash missing integrity, got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_finding_is_warning(self):
        assert self.findings[0].severity == Severity.WARNING

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
        assert len(self.findings) == 1, "Expected 1 finding for lodash missing integrity, got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_finding_is_warning(self):
        assert self.findings[0].severity == Severity.WARNING

    def test_finding_message_contains_package(self):
        assert "lodash" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "pnpm_lock"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)

    def test_finding_has_line_number(self):
        assert self.findings[0].line > 0
